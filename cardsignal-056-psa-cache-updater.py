from pathlib import Path
import json
import os

root = Path(os.environ['SOURCE_DIR'])
PUBLIC_KEY = 'dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEVBNkE3NDdDODQ1QUE5MjYKUldRbXFWcUVmSFJxNmtvYmwvcDlZVWdlcld3VWd3c2lIdnhUbHo2YXFlUWVHMVd4dnU4VnFvMHgK'


def replace_once(text, old, new, label):
    if old not in text:
        raise RuntimeError(f'{label} marker not found')
    return text.replace(old, new, 1)

# Rust backend
path = root / 'src-tauri/src/lib.rs'
text = path.read_text(encoding='utf-8').replace('\r\n', '\n')
text = replace_once(text,
    'use reqwest::header::{ACCEPT, ACCEPT_LANGUAGE, AUTHORIZATION, REFERER, USER_AGENT};',
    'use reqwest::header::{ACCEPT, ACCEPT_LANGUAGE, AUTHORIZATION, RETRY_AFTER, USER_AGENT};',
    'reqwest imports')
text = replace_once(text, 'use tauri::{AppHandle, Manager, State};',
                    'use tauri::{AppHandle, Manager, State};\nuse tauri_plugin_updater::UpdaterExt;',
                    'updater import')
text = replace_once(text, 'const APP_VERSION: &str = "0.5.5";', 'const APP_VERSION: &str = "0.5.6";', 'version')
text = replace_once(text, '    "auctions",\n];', '    "auctions",\n    "psaCache",\n];', 'cache store')

psa_start = text.index('#[tauri::command]\nfn psa_status')
psa_end = text.index('#[tauri::command]\nfn ebay_status', psa_start)
psa_block = r'''fn app_info_get(path: &Path, key: &str) -> CsResult<Option<String>> {
    let connection = open_connection(path)?;
    connection
        .query_row("SELECT value FROM app_info WHERE key = ?1", [key], |row| row.get(0))
        .optional()
        .map_err(CardSignalError::from)
}

fn app_info_set(path: &Path, key: &str, value: &str) -> CsResult<()> {
    let connection = open_connection(path)?;
    connection.execute(
        "INSERT INTO app_info(key, value) VALUES(?1, ?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        params![key, value],
    )?;
    Ok(())
}

fn clean_psa_number(number: &str) -> CsResult<String> {
    let clean = number.chars().filter(char::is_ascii_digit).collect::<String>();
    if !(6..=12).contains(&clean.len()) {
        return Err(message("Enter a valid numeric PSA certification number."));
    }
    Ok(clean)
}

fn psa_cache_key(cert_number: &str) -> String {
    format!("psa:{cert_number}")
}

fn psa_cache_get(path: &Path, cert_number: &str) -> CsResult<Option<Value>> {
    let connection = open_connection(path)?;
    let key = psa_cache_key(cert_number);
    let text = connection
        .query_row(
            "SELECT json FROM records WHERE store = 'psaCache' AND id = ?1",
            [key],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    let Some(text) = text else { return Ok(None); };
    let wrapper: Value = serde_json::from_str(&text)?;
    let mut payload = wrapper.get("payload").cloned().unwrap_or(wrapper);
    if let Some(object) = payload.as_object_mut() {
        object.insert("cached".to_string(), Value::Bool(true));
    }
    Ok(Some(payload))
}

fn psa_cache_put(path: &Path, cert_number: &str, payload: &Value) -> CsResult<()> {
    let key = psa_cache_key(cert_number);
    let wrapper = json!({
        "id": key,
        "certNumber": cert_number,
        "cachedAt": Utc::now().to_rfc3339(),
        "payload": payload,
    });
    let connection = open_connection(path)?;
    connection.execute(
        "INSERT INTO records(store, id, json, updated_at) VALUES('psaCache', ?1, ?2, ?3)
         ON CONFLICT(store, id) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at",
        params![psa_cache_key(cert_number), serde_json::to_string(&wrapper)?, Utc::now().to_rfc3339()],
    )?;
    Ok(())
}

fn psa_cache_count(path: &Path) -> CsResult<usize> {
    let connection = open_connection(path)?;
    let count: i64 = connection.query_row(
        "SELECT COUNT(*) FROM records WHERE store = 'psaCache'",
        [],
        |row| row.get(0),
    )?;
    Ok(count.max(0) as usize)
}

fn psa_usage(path: &Path) -> CsResult<(String, u64)> {
    let today = Utc::now().date_naive().to_string();
    let stored_date = app_info_get(path, "psa_api_usage_date")?.unwrap_or_default();
    let count = if stored_date == today {
        app_info_get(path, "psa_api_usage_count")?
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(0)
    } else {
        0
    };
    Ok((today, count))
}

fn increment_psa_usage(path: &Path) -> CsResult<u64> {
    let (today, count) = psa_usage(path)?;
    let next = count.saturating_add(1);
    app_info_set(path, "psa_api_usage_date", &today)?;
    app_info_set(path, "psa_api_usage_count", &next.to_string())?;
    Ok(next)
}

fn active_psa_block(path: &Path) -> CsResult<Option<DateTime<Utc>>> {
    let Some(value) = app_info_get(path, "psa_api_blocked_until")? else { return Ok(None); };
    let parsed = DateTime::parse_from_rfc3339(&value)
        .ok()
        .map(|value| value.with_timezone(&Utc));
    Ok(parsed.filter(|value| *value > Utc::now()))
}

fn set_psa_block(path: &Path, retry_after: Option<&str>) -> CsResult<DateTime<Utc>> {
    let seconds = retry_after
        .and_then(|value| value.trim().parse::<i64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(86_400);
    let until = Utc::now() + chrono::Duration::seconds(seconds);
    app_info_set(path, "psa_api_blocked_until", &until.to_rfc3339())?;
    Ok(until)
}

#[tauri::command]
fn psa_status(state: State<'_, AppState>) -> Result<Value, String> {
    let (call_date, calls_today) = psa_usage(&state.database_path).map_err(String::from)?;
    let blocked_until = active_psa_block(&state.database_path)
        .map_err(String::from)?
        .map(|value| value.to_rfc3339());
    Ok(json!({
        "configured": get_secret("psa_api_token").map_err(String::from)?.is_some(),
        "callsToday": calls_today,
        "callDate": call_date,
        "cacheCount": psa_cache_count(&state.database_path).map_err(String::from)?,
        "blockedUntil": blocked_until,
    }))
}

fn normalize_psa_token(input: &str) -> String {
    let mut candidate = input.trim().to_string();
    if let Ok(value) = serde_json::from_str::<Value>(&candidate) {
        for key in ["access_token", "accessToken", "token"] {
            if let Some(value) = value.get(key).and_then(Value::as_str) {
                candidate = value.to_string();
                break;
            }
        }
    }
    candidate = candidate
        .trim()
        .trim_matches(|character| character == '"' || character == '\'')
        .trim()
        .to_string();
    if candidate.to_ascii_lowercase().starts_with("authorization:") {
        candidate = candidate
            .split_once(':')
            .map(|(_, value)| value.trim().to_string())
            .unwrap_or(candidate);
    }
    if candidate.to_ascii_lowercase().starts_with("bearer ") {
        candidate = candidate[7..].trim().to_string();
    }
    candidate
        .trim_matches(|character| character == '"' || character == '\'')
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect()
}

#[tauri::command]
fn psa_set_token(token: String) -> Result<Value, String> {
    let clean = normalize_psa_token(&token);
    set_secret("psa_api_token", &clean).map_err(String::from)?;
    Ok(json!({
        "configured": !clean.is_empty(),
        "message": if clean.is_empty() { "PSA token removed." } else { "PSA token saved in Windows Credential Manager." }
    }))
}

#[tauri::command]
async fn psa_lookup(
    number: String,
    force_refresh: Option<bool>,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let clean = clean_psa_number(&number).map_err(String::from)?;
    if !force_refresh.unwrap_or(false) {
        if let Some(payload) = psa_cache_get(&state.database_path, &clean).map_err(String::from)? {
            return Ok(payload);
        }
    }

    if let Some(until) = active_psa_block(&state.database_path).map_err(String::from)? {
        return Err(format!(
            "PSA_FALLBACK_AVAILABLE::PSA API calls are paused until {} because PSA reported a quota limit. No API request was made.",
            until.to_rfc3339()
        ));
    }

    let stored_token = get_secret("psa_api_token")
        .map_err(String::from)?
        .ok_or_else(|| "PSA_FALLBACK_AVAILABLE::PSA API access is not configured. You can use the public cert page for this one lookup.".to_string())?;
    let token = normalize_psa_token(&stored_token);
    if token.is_empty() {
        return Err("PSA_FALLBACK_AVAILABLE::PSA API access is not configured. You can use the public cert page for this one lookup.".to_string());
    }

    let url = format!("https://api.psacard.com/publicapi/cert/GetByCertNumber/{clean}");
    increment_psa_usage(&state.database_path).map_err(String::from)?;
    let response = state
        .http
        .get(url)
        .header(AUTHORIZATION, format!("bearer {token}"))
        .header(ACCEPT, "application/json")
        .send()
        .await
        .map_err(|error| format!("PSA_FALLBACK_AVAILABLE::PSA API request failed: {error}"))?;
    let status = response.status();
    let retry_after = response
        .headers()
        .get(RETRY_AFTER)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let text = response.text().await.map_err(|error| {
        format!("PSA_FALLBACK_AVAILABLE::PSA returned an unreadable response: {error}")
    })?;

    if status.as_u16() == 204 {
        return Err("PSA_FALLBACK_AVAILABLE::PSA returned an empty response for this cert.".to_string());
    }
    if !status.is_success() {
        let code = status.as_u16();
        let lower = text.to_lowercase();
        let quota = code == 429
            || lower.contains("quota exceeded")
            || lower.contains("maximum admitted")
            || lower.contains("per day")
            || (lower.contains("quota") && lower.contains("limit"));
        let detail = truncate(&text, 280);
        if quota {
            let until = set_psa_block(&state.database_path, retry_after.as_deref()).map_err(String::from)?;
            return Err(format!(
                "PSA_FALLBACK_AVAILABLE::PSA reported that the daily API allowance is exhausted. API calls are paused until {}. PSA response: {}",
                until.to_rfc3339(), detail
            ));
        }
        return Err(format!(
            "PSA_FALLBACK_AVAILABLE::PSA returned HTTP {code}. No retry was attempted. PSA response: {detail}"
        ));
    }

    let record: Value = serde_json::from_str(&text).map_err(|error| {
        format!("PSA_FALLBACK_AVAILABLE::PSA returned unreadable JSON: {error}. Response: {}", truncate(&text, 240))
    })?;
    let payload = json!({
        "certNumber": clean,
        "source": "PSA Public API",
        "sourceUrl": format!("https://www.psacard.com/cert/{clean}/psa"),
        "retrievedAt": Utc::now().to_rfc3339(),
        "cached": false,
        "record": record,
    });
    psa_cache_put(&state.database_path, &clean, &payload).map_err(String::from)?;
    Ok(payload)
}

fn node_text(element: scraper::ElementRef<'_>) -> String {
    element
        .text()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

fn normalize_psa_label(value: &str) -> String {
    value
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

fn value_for(map: &HashMap<String, String>, aliases: &[&str]) -> String {
    aliases
        .iter()
        .find_map(|alias| map.get(*alias).cloned())
        .unwrap_or_default()
}

#[tauri::command]
async fn psa_public_page_lookup(number: String, state: State<'_, AppState>) -> Result<Value, String> {
    let clean = clean_psa_number(&number).map_err(String::from)?;
    if let Some(payload) = psa_cache_get(&state.database_path, &clean).map_err(String::from)? {
        return Ok(payload);
    }

    let source_url = format!("https://www.psacard.com/cert/{clean}/psa");
    let response = state
        .http
        .get(&source_url)
        .header(
            USER_AGENT,
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        )
        .header(ACCEPT, "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        .header(ACCEPT_LANGUAGE, "en-US,en;q=0.9")
        .send()
        .await
        .map_err(|error| format!("Could not load the PSA public cert page: {error}"))?;
    let status = response.status();
    let html = response.text().await.map_err(|error| format!("Could not read the PSA public cert page: {error}"))?;
    if !status.is_success() {
        return Err(format!("PSA public cert page returned HTTP {}: {}", status.as_u16(), truncate(&html, 240)));
    }

    let parsed = {
        let document = Html::parse_document(&html);
        let dt_selector = Selector::parse("dt").map_err(|_| "Could not prepare PSA label parser.".to_string())?;
        let dd_selector = Selector::parse("dd").map_err(|_| "Could not prepare PSA value parser.".to_string())?;
        let mut fields = HashMap::<String, String>::new();
        let labels = document.select(&dt_selector).map(node_text).collect::<Vec<_>>();
        let values = document.select(&dd_selector).map(node_text).collect::<Vec<_>>();
        for (label, value) in labels.into_iter().zip(values.into_iter()) {
            if !label.is_empty() && !value.is_empty() {
                fields.entry(normalize_psa_label(&label)).or_insert(value);
            }
        }

        let script_selector = Selector::parse("script[type='application/json']")
            .map_err(|_| "Could not prepare PSA data parser.".to_string())?;
        let embedded = document.select(&script_selector).find_map(|script| {
            let text = script.text().collect::<String>();
            if !text.contains(&clean) { return None; }
            serde_json::from_str::<Value>(&text).ok()
        });

        let image_selector = Selector::parse("img").map_err(|_| "Could not prepare PSA image parser.".to_string())?;
        let image_url = document.select(&image_selector).find_map(|image| {
            let alt = image.value().attr("alt").unwrap_or_default().to_lowercase();
            let src = image.value().attr("src").or_else(|| image.value().attr("data-src"))?;
            if src.starts_with("http") && (alt.contains("front") || alt.contains("cert") || src.to_lowercase().contains("cert")) {
                Some(src.to_string())
            } else {
                None
            }
        }).unwrap_or_default();
        (fields, embedded, image_url)
    };

    let (fields, embedded, image_url) = parsed;
    let record = if let Some(value) = embedded {
        value
    } else {
        let grade = value_for(&fields, &["itemgrade", "grade"]);
        let year = value_for(&fields, &["year"]);
        let brand = value_for(&fields, &["brandtitle", "brand", "title"]);
        let subject = value_for(&fields, &["subject"]);
        let card_number = value_for(&fields, &["cardnumber"]);
        let category = value_for(&fields, &["category"]);
        let variety = value_for(&fields, &["varietypedigree", "variety", "pedigree"]);
        let population = value_for(&fields, &["psapopulation", "population"]);
        let population_higher = value_for(&fields, &["psapophigher", "populationhigher"]);
        if subject.is_empty() && grade.is_empty() {
            return Err("CardSignal loaded the PSA page, but its current layout did not expose readable item fields. No data was saved.".to_string());
        }
        json!({
            "IsValidRequest": true,
            "PSACert": {
                "CertNumber": clean,
                "CardGrade": grade,
                "Year": year,
                "Brand": brand,
                "Subject": subject,
                "CardNumber": card_number,
                "Category": category,
                "Variety": variety,
                "TotalPopulation": population,
                "PopulationHigher": population_higher,
                "FrontImageUrl": image_url,
            }
        })
    };

    let payload = json!({
        "certNumber": clean,
        "source": "PSA public cert page (user initiated)",
        "sourceUrl": source_url,
        "retrievedAt": Utc::now().to_rfc3339(),
        "cached": false,
        "record": record,
    });
    psa_cache_put(&state.database_path, &clean, &payload).map_err(String::from)?;
    Ok(payload)
}

#[tauri::command]
async fn updater_check(app: AppHandle) -> Result<Value, String> {
    let updater = app.updater().map_err(|error| error.to_string())?;
    let update = updater.check().await.map_err(|error| error.to_string())?;
    Ok(match update {
        Some(update) => json!({
            "available": true,
            "version": update.version,
            "currentVersion": update.current_version,
            "notes": update.body,
            "date": update.date.map(|value| value.to_string()),
        }),
        None => json!({
            "available": false,
            "currentVersion": APP_VERSION,
        }),
    })
}

#[tauri::command]
async fn updater_install(app: AppHandle) -> Result<Value, String> {
    let updater = app.updater().map_err(|error| error.to_string())?;
    let Some(update) = updater.check().await.map_err(|error| error.to_string())? else {
        return Ok(json!({ "installed": false, "message": "CardSignal is already current." }));
    };
    let version = update.version.clone();
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|error| error.to_string())?;
    app.restart();
    #[allow(unreachable_code)]
    Ok(json!({ "installed": true, "version": version }))
}

'''
text = text[:psa_start] + psa_block + text[psa_end:]
text = replace_once(text,
    '        .plugin(tauri_plugin_opener::init())',
    '        .plugin(tauri_plugin_opener::init())\n        .plugin(tauri_plugin_updater::Builder::new().build())',
    'updater plugin')
text = replace_once(text,
    '            psa_lookup,\n            ebay_status,',
    '            psa_lookup,\n            psa_public_page_lookup,\n            updater_check,\n            updater_install,\n            ebay_status,',
    'command registration')
path.write_text(text, encoding='utf-8', newline='\n')

# Cargo
path = root / 'src-tauri/Cargo.toml'
text = path.read_text(encoding='utf-8')
text = text.replace('version = "0.5.5"', 'version = "0.5.6"', 1)
text = replace_once(text, 'tauri-plugin-single-instance = "2"', 'tauri-plugin-single-instance = "2"\ntauri-plugin-updater = "2"', 'cargo updater')
path.write_text(text, encoding='utf-8', newline='\n')

# Tauri config
path = root / 'src-tauri/tauri.conf.json'
config = json.loads(path.read_text(encoding='utf-8'))
config['version'] = '0.5.6'
config.setdefault('bundle', {})['createUpdaterArtifacts'] = False
config['plugins'] = {
    **config.get('plugins', {}),
    'updater': {
        'pubkey': PUBLIC_KEY,
        'endpoints': ['https://github.com/jboll3/PSA-Card-Track/releases/latest/download/latest.json'],
        'windows': {'installMode': 'passive'},
    },
}
path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')

# Frontend app.js
path = root / 'dist/src/app.js'
text = path.read_text(encoding='utf-8').replace('\r\n', '\n')
text = replace_once(text, '  psaLookup: null,\n  ebayStatus: null,', '  psaLookup: null,\n  psaStatus: null,\n  updateInfo: null,\n  ebayStatus: null,', 'frontend state')

start = text.index('async function fetchPsaCert(')
end = text.index('async function refreshEbayStatus(', start)
replacement = r'''function parsePsaError(error) {
  const message = errorMessage(error, 'PSA lookup failed for an unknown reason.');
  const marker = 'PSA_FALLBACK_AVAILABLE::';
  return { message: message.startsWith(marker) ? message.slice(marker.length) : message, fallbackAvailable: message.startsWith(marker) };
}

async function fetchPsaCert(certNumber, { forceRefresh = false } = {}) {
  const clean = String(certNumber || '').replace(/\D/g, '');
  if (clean.length < 6) throw new Error('Enter a valid numeric PSA certification number.');
  const payload = await invoke('psa_lookup', { number: clean, forceRefresh });
  return { payload, normalized: normalizePsaCertResponse(payload) };
}

async function fetchPsaPublicPage(certNumber) {
  const clean = String(certNumber || '').replace(/\D/g, '');
  if (clean.length < 6) throw new Error('Enter a valid numeric PSA certification number.');
  const payload = await invoke('psa_public_page_lookup', { number: clean });
  return { payload, normalized: normalizePsaCertResponse(payload) };
}

function formatPsaStatus(payload) {
  const pieces = [];
  if (payload.configured) pieces.push('API token configured');
  else pieces.push('API token not configured');
  pieces.push(`${formatNumber(payload.callsToday || 0)} API call${Number(payload.callsToday) === 1 ? '' : 's'} today`);
  pieces.push(`${formatNumber(payload.cacheCount || 0)} cached cert${Number(payload.cacheCount) === 1 ? '' : 's'}`);
  if (payload.blockedUntil) pieces.push(`API paused until ${new Date(payload.blockedUntil).toLocaleString()}`);
  return pieces.join(' · ');
}

async function refreshPsaStatus(targetSelector = '#psa-token-status') {
  const status = $(targetSelector);
  try {
    const payload = await invoke('psa_status');
    state.psaStatus = payload;
    if (status) {
      status.textContent = formatPsaStatus(payload);
      status.classList.toggle('good', Boolean(payload.configured) && !payload.blockedUntil);
      status.classList.toggle('warn', !payload.configured || Boolean(payload.blockedUntil));
    }
    return Boolean(payload.configured);
  } catch (error) {
    if (status) status.textContent = errorMessage(error);
    return false;
  }
}

async function savePsaToken(clear = false) {
  clear = clear === true;
  const input = $('#psa-token-setting');
  const token = clear ? '' : String(input?.value || '').trim().replace(/^bearer\s+/i, '');
  if (!clear && !token) {
    toast('PSA token missing', 'Paste the access token generated by PSA, not your PSA password.');
    return;
  }
  const button = clear ? $('#clear-psa-token') : $('#save-psa-token');
  const original = button?.textContent;
  if (button) { button.disabled = true; button.textContent = clear ? 'Removing…' : 'Saving…'; }
  try {
    const payload = await invoke('psa_set_token', { token });
    if (input) input.value = '';
    await refreshPsaStatus();
    toast(clear ? 'PSA token removed' : 'PSA lookup configured', payload.message || 'PSA settings updated.');
  } catch (error) {
    toast('PSA setup failed', errorMessage(error), 7000);
  } finally {
    if (button) { button.disabled = false; button.textContent = original; }
  }
}

function renderUpdateStatus() {
  const target = $('#update-status');
  const install = $('#install-update');
  if (!target) return;
  if (!state.updateInfo) {
    target.textContent = 'CardSignal checks for signed updates when it starts. Installation always requires your click.';
    install?.classList.add('hidden');
    return;
  }
  if (state.updateInfo.available) {
    target.textContent = `CardSignal ${state.updateInfo.version} is available${state.updateInfo.notes ? ` · ${state.updateInfo.notes}` : ''}`;
    target.className = 'mini-note good';
    install?.classList.remove('hidden');
  } else {
    target.textContent = `CardSignal ${state.updateInfo.currentVersion || '0.5.6'} is current.`;
    target.className = 'mini-note good';
    install?.classList.add('hidden');
  }
}

async function checkForUpdates(interactive = true) {
  const button = $('#check-for-updates');
  const original = button?.textContent;
  if (button) { button.disabled = true; button.textContent = 'Checking…'; }
  try {
    state.updateInfo = await invoke('updater_check');
    renderUpdateStatus();
    if (state.updateInfo.available) toast('CardSignal update available', `Version ${state.updateInfo.version} is ready to install.`);
    else if (interactive) toast('CardSignal is current', `Version ${state.updateInfo.currentVersion || '0.5.6'} is installed.`);
  } catch (error) {
    if (interactive) toast('Update check failed', errorMessage(error), 7500);
    const target = $('#update-status');
    if (target && interactive) { target.textContent = errorMessage(error); target.className = 'mini-note warn'; }
  } finally {
    if (button) { button.disabled = false; button.textContent = original; }
  }
}

async function installUpdate() {
  const button = $('#install-update');
  const original = button?.textContent;
  if (button) { button.disabled = true; button.textContent = 'Installing…'; }
  try {
    await invoke('updater_install');
  } catch (error) {
    toast('Update installation failed', errorMessage(error), 9000);
    if (button) { button.disabled = false; button.textContent = original; }
  }
}

'''
text = text[:start] + replacement + text[end:]

# Replace result/lookup block
start = text.index('function setSlabFormValue(')
end = text.index('function openSlabModal(', start)
replacement = r'''function setSlabFormValue(name, value) {
  const field = $('#slab-form')?.elements?.namedItem(name);
  if (!field || value == null || value === '') return;
  field.value = String(value);
}

function psaFormFields(slab) {
  return {
    game: slab.game,
    cardName: slab.cardName,
    setName: slab.setName,
    cardNumber: slab.cardNumber,
    grader: 'PSA',
    grade: slab.grade,
    certNumber: slab.certNumber,
    population: slab.population || '',
    imageUrl: slab.imageUrl,
    notes: slab.notes,
  };
}

function applyPsaFields(slab, only = null) {
  const values = psaFormFields(slab);
  Object.entries(values).forEach(([name, value]) => {
    if (!only || only === name) setSlabFormValue(name, value);
  });
}

function renderPsaLookupResult(slab, { reviewOnly = false, source = '', cached = false } = {}) {
  const target = $('#psa-lookup-result');
  const values = psaFormFields(slab);
  const rows = [
    ['cardName', 'Card name'], ['setName', 'Set'], ['cardNumber', 'Card number'],
    ['grade', 'Grade'], ['certNumber', 'Cert'], ['population', 'Population'], ['game', 'Game'],
  ].filter(([key]) => values[key] !== '' && values[key] != null);
  target.innerHTML = `<div class="summary-banner psa-result"><div class="empty-icon" style="margin:0">${reviewOnly ? '?' : '✓'}</div><div><strong>${escapeHtml(slab.cardName)} · PSA ${escapeHtml(slab.grade)}</strong><span>${escapeHtml(source || 'PSA')}${cached ? ' · cached' : ''} · Cert ${escapeHtml(slab.certNumber)}</span></div></div>
    ${reviewOnly ? `<div class="preview-table" style="margin-top:12px"><table><tbody>${rows.map(([key, label]) => `<tr><th>${escapeHtml(label)}</th><td>${escapeHtml(values[key])}</td><td><button type="button" class="ghost-button small" data-apply-psa-field="${key}">Use</button></td></tr>`).join('')}</tbody></table></div><div class="button-row" style="margin-top:12px"><button type="button" class="primary-button" id="apply-all-psa-fields">Use all fields</button><a class="ghost-button" href="${escapeHtml(slab.sourceUrl)}" target="_blank" rel="noopener">View official page</a></div>` : `<div class="button-row" style="margin-top:10px"><a class="ghost-button small" href="${escapeHtml(slab.sourceUrl)}" target="_blank" rel="noopener">Verify at PSA</a></div>`}`;
  $$('[data-apply-psa-field]', target).forEach((button) => button.addEventListener('click', () => {
    applyPsaFields(slab, button.dataset.applyPsaField);
    toast('Field copied', `${button.closest('tr')?.querySelector('th')?.textContent || 'PSA field'} was copied into the form.`);
  }));
  $('#apply-all-psa-fields')?.addEventListener('click', () => {
    applyPsaFields(slab);
    toast('PSA fields copied', 'Review the form, then add purchase price or estimated value.');
    $('#slab-form').elements.namedItem('purchasePrice')?.focus();
  });
}

async function usePsaPublicPage(certNumber) {
  const status = $('#psa-lookup-status');
  const button = $('#use-psa-public-page');
  const original = button?.textContent;
  if (button) { button.disabled = true; button.textContent = 'Reading page…'; }
  status.textContent = 'Loading the one public PSA cert page you requested…';
  try {
    const result = await fetchPsaPublicPage(certNumber);
    state.psaLookup = { ...result, reviewOnly: true };
    renderPsaLookupResult(result.normalized, {
      reviewOnly: true,
      source: result.payload.source,
      cached: result.payload.cached,
    });
    status.textContent = 'Public-page results are ready. Use individual fields or copy all of them into the form.';
    status.className = 'mini-note good';
    await refreshPsaStatus();
  } catch (error) {
    const message = errorMessage(error);
    status.textContent = message;
    status.className = 'mini-note warn';
    toast('Public cert page could not be read', message, 10000);
  } finally {
    if (button?.isConnected) { button.disabled = false; button.textContent = original; }
  }
}

async function lookupPsaCert() {
  const input = $('#psa-cert-lookup');
  const button = $('#lookup-psa-cert');
  const status = $('#psa-lookup-status');
  const certNumber = String(input?.value || '').replace(/\D/g, '');
  if (!certNumber) {
    toast('Certification number missing', 'Enter the PSA number printed on the slab label.');
    input?.focus();
    return;
  }
  const original = button.textContent;
  button.disabled = true;
  button.textContent = 'Looking up…';
  status.textContent = 'Checking CardSignal cache, then making at most one PSA API request…';
  $('#psa-lookup-result').innerHTML = '';
  try {
    const result = await fetchPsaCert(certNumber);
    state.psaLookup = { ...result, reviewOnly: false };
    const slab = result.normalized;
    applyPsaFields(slab);
    renderPsaLookupResult(slab, { source: result.payload.source, cached: result.payload.cached });
    status.textContent = result.payload.cached
      ? 'Saved PSA record loaded from CardSignal cache. No API call was made.'
      : 'Official PSA API record retrieved with one request and saved permanently.';
    status.className = 'mini-note good';
    $('#slab-form').elements.namedItem('purchasePrice')?.focus();
    await refreshPsaStatus();
  } catch (error) {
    state.psaLookup = null;
    const parsed = parsePsaError(error);
    console.error('PSA cert lookup failed:', error);
    status.textContent = parsed.message;
    status.className = 'mini-note warn';
    if (parsed.fallbackAvailable) {
      $('#psa-lookup-result').innerHTML = `<div class="inline-callout"><span>The API lookup stopped without retrying. CardSignal can read this one public cert page after you click.</span><button type="button" class="primary-button small" id="use-psa-public-page">Use public cert page</button></div>`;
      $('#use-psa-public-page').onclick = () => usePsaPublicPage(certNumber);
    }
    toast('PSA API lookup stopped', parsed.message, 10000);
    await refreshPsaStatus();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

'''
text = text[:start] + replacement + text[end:]

# Force refresh existing uses API exactly once
text = text.replace('const { normalized } = await fetchPsaCert(slab.certNumber);', 'const { normalized } = await fetchPsaCert(slab.certNumber, { forceRefresh: true });', 1)

# Settings card and bindings
old_card = '<div class="settings-card"><h3>PSA certificate lookup</h3><p>Paste a PSA Public API access token, not your PSA password. CardSignal encrypts it with your Windows account and keeps it outside browser storage.</p><div class="setting-stack"><input id="psa-token-setting" type="password" autocomplete="off" placeholder="Paste PSA bearer token" /><div class="button-row"><button class="primary-button" id="save-psa-token">Save token</button><button class="ghost-button" id="clear-psa-token">Remove token</button><a class="ghost-button" href="https://www.psacard.com/publicapi" target="_blank" rel="noopener">Get PSA API access</a></div><div class="mini-note" id="psa-token-status">Checking local PSA configuration…</div></div></div>'
new_card = '<div class="settings-card"><h3>PSA certificate lookup</h3><p>CardSignal checks its permanent cache first and makes no more than one API request for a new cert. A public-page fallback is only used after you explicitly click it.</p><div class="setting-stack"><input id="psa-token-setting" type="password" autocomplete="off" placeholder="Paste PSA bearer token" /><div class="button-row"><button class="primary-button" id="save-psa-token">Save token</button><button class="ghost-button" id="clear-psa-token">Remove token</button><a class="ghost-button" href="https://www.psacard.com/publicapi" target="_blank" rel="noopener">Get PSA API access</a></div><div class="mini-note" id="psa-token-status">Checking PSA usage and cache…</div></div></div>\n      <div class="settings-card"><h3>App updates</h3><p>CardSignal checks GitHub Releases for signed updates. It never installs an update without your click.</p><div class="button-row"><button class="primary-button" id="check-for-updates">Check for updates</button><button class="ghost-button hidden" id="install-update">Install and restart</button></div><div class="mini-note" id="update-status">Loading update status…</div></div>'
text = replace_once(text, old_card, new_card, 'settings updater card')
text = replace_once(text,
    "  $('#clear-psa-token')?.addEventListener('click', () => savePsaToken(true));",
    "  $('#clear-psa-token')?.addEventListener('click', () => savePsaToken(true));\n  $('#check-for-updates')?.addEventListener('click', () => checkForUpdates(true));\n  $('#install-update')?.addEventListener('click', installUpdate);",
    'updater bindings')
text = replace_once(text,
    "  if (state.view === 'settings') { refreshPsaStatus(); refreshEbayStatus(); refreshHealth(); }",
    "  if (state.view === 'settings') { refreshPsaStatus(); refreshEbayStatus(); refreshHealth(); renderUpdateStatus(); }",
    'settings updater render')
text = replace_once(text,
    '  maybeAutoSyncDemand();\n}',
    '  maybeAutoSyncDemand();\n  setTimeout(() => checkForUpdates(false), 3000);\n}',
    'startup updater check')
text = text.replace('0.5.5', '0.5.6')
path.write_text(text, encoding='utf-8', newline='\n')

# HTML / package / docs version and explanatory text
for rel in ['dist/index.html', 'package.json', 'README.md', 'CHANGELOG.md']:
    path = root / rel
    if path.exists():
        value = path.read_text(encoding='utf-8').replace('0.5.5', '0.5.6')
        value = value.replace('CardSignal retrieves the official PSA record and fills the form below. Review the match before saving.',
                              'CardSignal checks its permanent cache, then makes at most one PSA API request. If PSA blocks the API, you can explicitly request a one-page public lookup and review the parsed fields before copying them.')
        path.write_text(value, encoding='utf-8', newline='\n')

# Sanity checks
backend = (root / 'src-tauri/src/lib.rs').read_text(encoding='utf-8')
frontend = (root / 'dist/src/app.js').read_text(encoding='utf-8')
checks = {
    'one API send': 'increment_psa_usage(&state.database_path)',
    'quota lock': 'psa_api_blocked_until',
    'public page command': 'async fn psa_public_page_lookup',
    'cache store': '"psaCache"',
    'updater command': 'async fn updater_check',
    'manual fallback button': 'Use public cert page',
    'review copy all': 'Use all fields',
}
for label, marker in checks.items():
    if marker not in backend and marker not in frontend:
        raise RuntimeError(f'{label} missing')
if 'swagger_compatible' in backend or 'PSA Swagger-compatible' in backend:
    raise RuntimeError('multi-request Swagger retry code remains')
print('Applied CardSignal 0.5.6 PSA cache, manual public-page fallback, and updater support.')
