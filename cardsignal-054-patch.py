from pathlib import Path
import os

root = Path(os.environ["SOURCE_DIR"])


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"{label} marker not found")
    return text.replace(old, new, 1)


backend_path = root / "src-tauri/src/lib.rs"
backend = backend_path.read_text(encoding="utf-8").replace("\r\n", "\n")
backend = replace_once(
    backend,
    "use reqwest::header::{ACCEPT, ACCEPT_LANGUAGE, AUTHORIZATION, USER_AGENT};",
    "use reqwest::header::{ACCEPT, ACCEPT_LANGUAGE, AUTHORIZATION, CONTENT_TYPE};",
    "reqwest header import",
)
backend = replace_once(
    backend,
    'const APP_VERSION: &str = "0.5.0";',
    'const APP_VERSION: &str = "0.5.4";',
    "app version",
)
backend = replace_once(
    backend,
    "        let mut backup = rusqlite::backup::Backup::new(&source, &mut target)?;",
    "        let backup = rusqlite::backup::Backup::new(&source, &mut target)?;",
    "backup mutability",
)
backend = replace_once(
    backend,
    "async fn ebay_access_token(state: &AppState) -> CsResult<String> {\n    if let Some(cached) = state.ebay_token.lock().clone() {",
    "async fn ebay_access_token(state: &AppState) -> CsResult<String> {\n    let cached_token = { state.ebay_token.lock().clone() };\n    if let Some(cached) = cached_token {",
    "eBay token lock",
)

psa_start = backend.index("#[tauri::command]\nfn psa_set_token")
psa_end = backend.index("#[tauri::command]\nfn ebay_status", psa_start)
psa_block = r'''fn normalize_psa_token(input: &str) -> String {
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

async fn psa_api_request(
    state: &AppState,
    cert_number: &str,
    token: &str,
    scheme: &str,
    method: &str,
) -> CsResult<(reqwest::StatusCode, String)> {
    let url = format!("https://api.psacard.com/publicapi/cert/{method}/{cert_number}");
    let response = state
        .http
        .get(url)
        .header(AUTHORIZATION, format!("{scheme} {token}"))
        .header(ACCEPT, "application/json")
        .header(CONTENT_TYPE, "application/json")
        .send()
        .await?;
    let status = response.status();
    let text = response.text().await?;
    Ok((status, text))
}

async fn psa_api_lookup(
    state: &AppState,
    cert_number: &str,
    token: &str,
) -> CsResult<(reqwest::StatusCode, String)> {
    let mut result =
        psa_api_request(state, cert_number, token, "bearer", "GetByCertNumber").await?;
    if matches!(result.0.as_u16(), 401 | 403 | 500) {
        let retry =
            psa_api_request(state, cert_number, token, "Bearer", "GetByCertNumber").await?;
        if retry.0.is_success() || retry.0 != result.0 || retry.1 != result.1 {
            result = retry;
        }
    }
    if matches!(result.0.as_u16(), 404 | 405) {
        result = psa_api_request(
            state,
            cert_number,
            token,
            "bearer",
            "GetByCertNumberForFileAppend",
        )
        .await?;
    }
    Ok(result)
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
async fn psa_lookup(number: String, state: State<'_, AppState>) -> Result<Value, String> {
    let clean = number.chars().filter(char::is_ascii_digit).collect::<String>();
    if !(6..=12).contains(&clean.len()) {
        return Err("Enter a valid numeric PSA certification number.".to_string());
    }
    let stored_token = get_secret("psa_api_token")
        .map_err(String::from)?
        .ok_or_else(|| "PSA_API_TOKEN_MISSING".to_string())?;
    let token = normalize_psa_token(&stored_token);
    if token.is_empty() {
        return Err("PSA_API_TOKEN_MISSING".to_string());
    }

    let (status, text) = psa_api_lookup(&state, &clean, &token)
        .await
        .map_err(String::from)?;
    if status.as_u16() == 204 {
        return Err("PSA returned an empty response. Check the certification number and retry.".to_string());
    }
    if !status.is_success() {
        let code = status.as_u16();
        let detail = truncate(&text, 240);
        let lower = text.to_lowercase();
        let explanation = match code {
            401 | 403 => format!("PSA rejected the access token (HTTP {code}). Generate a fresh token on PSA's Public API page, save it again, and retry. PSA response: {detail}"),
            429 => format!("PSA API lookup limit reached (HTTP 429). Retry after PSA resets the quota. PSA response: {detail}"),
            500 if lower.contains("quota") || lower.contains("limit") => format!("PSA API lookup quota was exceeded (HTTP 500). PSA response: {detail}"),
            500 => format!("PSA rejected the credentials or returned a server error (HTTP 500). PSA says this status usually means invalid credentials. Generate a fresh access token and retry. PSA response: {detail}"),
            _ => format!("PSA lookup returned HTTP {code}: {detail}"),
        };
        return Err(explanation);
    }

    let record: Value = serde_json::from_str(&text).map_err(|error| {
        format!(
            "PSA returned unreadable JSON: {error}. Response: {}",
            truncate(&text, 240)
        )
    })?;
    Ok(json!({
        "certNumber": clean,
        "source": "PSA Public API",
        "sourceUrl": format!("https://www.psacard.com/cert/{clean}"),
        "retrievedAt": Utc::now().to_rfc3339(),
        "record": record
    }))
}

'''
backend = backend[:psa_start] + psa_block + backend[psa_end:]

start_marker = "        let document = Html::parse_document(&html);"
end_marker = "\n\n        let mut found_for_month = 0usize;"
start = backend.index(start_marker)
end = backend.index(end_marker, start)
replacement = '''        // scraper::Html is not Send, so it must be dropped before the next await.
        let candidates = {
            let document = Html::parse_document(&html);
            let base = Url::parse(&article_url)?;
            let mut candidates = Vec::<String>::new();
            let mut seen_links = HashSet::<String>::new();
            for anchor in document.select(&href_selector) {
                let Some(href) = anchor.value().attr("href") else { continue; };
                let resolved = Url::parse(href).or_else(|_| base.join(href));
                let Ok(url) = resolved else { continue; };
                let text = url.as_str().to_string();
                let lower = text.to_lowercase();
                if (lower.contains("bit.ly/") || lower.contains(".csv") || lower.contains("hubspotusercontent"))
                    && seen_links.insert(text.clone())
                {
                    candidates.push(text);
                }
            }
            candidates
        };'''
backend = backend[:start] + replacement + backend[end:]
backend_path.write_text(backend, encoding="utf-8", newline="\n")

cargo_path = root / "src-tauri/Cargo.toml"
cargo = cargo_path.read_text(encoding="utf-8")
cargo = replace_once(cargo, '"rustls-tls"', '"native-tls"', "Windows native TLS")
cargo_path.write_text(cargo, encoding="utf-8", newline="\n")

main_path = root / "src-tauri/src/main.rs"
main = main_path.read_text(encoding="utf-8").replace("\r\n", "\n")
attribute = '#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]'
if not main.startswith(attribute):
    main = attribute + "\n\n" + main
main_path.write_text(main, encoding="utf-8", newline="\n")

for rel in [
    "package.json",
    "src-tauri/Cargo.toml",
    "src-tauri/tauri.conf.json",
    "README.md",
    "CHANGELOG.md",
    "dist/index.html",
    "dist/src/app.js",
]:
    path = root / rel
    if path.exists():
        path.write_text(
            path.read_text(encoding="utf-8").replace("0.5.0", "0.5.4"),
            encoding="utf-8",
            newline="\n",
        )

frontend_path = root / "dist/src/app.js"
frontend = frontend_path.read_text(encoding="utf-8").replace("\r\n", "\n")
frontend = replace_once(
    frontend,
    "  $('#save-psa-token')?.addEventListener('click', savePsaToken);",
    "  $('#save-psa-token')?.addEventListener('click', () => savePsaToken(false));",
    "PSA save binding",
)
frontend = replace_once(
    frontend,
    "  $('#save-ebay-credentials')?.addEventListener('click', saveEbayCredentials);",
    "  $('#save-ebay-credentials')?.addEventListener('click', () => saveEbayCredentials(false));",
    "eBay save binding",
)
frontend = replace_once(
    frontend,
    "async function savePsaToken(clear = false) {",
    "async function savePsaToken(clear = false) {\n  clear = clear === true;",
    "PSA save guard",
)
frontend = replace_once(
    frontend,
    "async function saveEbayCredentials(clear = false) {",
    "async function saveEbayCredentials(clear = false) {\n  clear = clear === true;",
    "eBay save guard",
)

toast_block = '''function toast(title, message = '', duration = 4200) {
  const element = document.createElement('div');
  element.className = 'toast';
  element.innerHTML = `<strong>${escapeHtml(title)}</strong>${message ? `<span>${escapeHtml(message)}</span>` : ''}`;
  $('#toast-stack').append(element);
  setTimeout(() => element.remove(), duration);
}'''
error_helper = '''

function errorMessage(error, fallback = 'Unexpected error.') {
  if (typeof error === 'string') return error.trim() || fallback;
  if (error && typeof error.message === 'string') return error.message.trim() || fallback;
  try {
    const text = String(error || '').trim();
    return text && text !== '[object Object]' ? text : fallback;
  } catch {
    return fallback;
  }
}'''
frontend = replace_once(frontend, toast_block, toast_block + error_helper, "error helper")

old_grade = "  const rawGrade = String(pickPsaValue(entries, ['gradeDescription', 'gradeDesc', 'grade', 'finalGrade']) || '').trim();\n  const gradeMatch = rawGrade.match(/(10|9\\.5|9|8\\.5|8|7\\.5|7|6\\.5|6|5\\.5|5|4\\.5|4|3\\.5|3|2\\.5|2|1\\.5|1)(?!.*\\d)/);\n  const grade = gradeMatch ? gradeMatch[1] : rawGrade;"
new_grade = "  const gradeDescription = String(pickPsaValue(entries, ['gradeDescription', 'gradeDesc']) || '').trim();\n  const cardGrade = String(pickPsaValue(entries, ['cardGrade', 'grade', 'finalGrade']) || '').trim();\n  const rawGrade = gradeDescription || cardGrade;\n  const gradeMatch = (cardGrade || rawGrade).match(/(10|9\\.5|9|8\\.5|8|7\\.5|7|6\\.5|6|5\\.5|5|4\\.5|4|3\\.5|3|2\\.5|2|1\\.5|1)(?!.*\\d)/);\n  const grade = gradeMatch ? gradeMatch[1] : (cardGrade || rawGrade);"
frontend = replace_once(frontend, old_grade, new_grade, "PSA CardGrade parsing")
frontend = frontend.replace(
    "    gradeLabel: rawGrade,", "    gradeLabel: gradeDescription || rawGrade,", 1
)

old_catch = '''  } catch (error) {
    state.psaLookup = null;
    status.textContent = error.message;
    status.className = 'mini-note warn';
    if (/configured|token/i.test(error.message)) {
      $('#psa-lookup-result').innerHTML = '<div class="inline-callout"><span>PSA access needs setup.</span><button type="button" class="ghost-button small" id="go-psa-settings">Open Settings</button></div>';
      $('#go-psa-settings').onclick = () => { closeModals(); navigate('settings'); };
    }
    toast('PSA lookup failed', error.message, 7500);
  } finally {'''
new_catch = '''  } catch (error) {
    state.psaLookup = null;
    const message = errorMessage(error, 'PSA lookup failed for an unknown reason.');
    console.error('PSA cert lookup failed:', error);
    status.textContent = message;
    status.className = 'mini-note warn';
    if (/configured|token|credential|authorization/i.test(message)) {
      $('#psa-lookup-result').innerHTML = '<div class="inline-callout"><span>PSA access needs setup.</span><button type="button" class="ghost-button small" id="go-psa-settings">Open Settings</button></div>';
      $('#go-psa-settings').onclick = () => { closeModals(); navigate('settings'); };
    }
    toast('PSA lookup failed', message, 12000);
  } finally {'''
frontend = replace_once(frontend, old_catch, new_catch, "PSA lookup error handling")
frontend = frontend.replace(
    "    toast('PSA refresh failed', error.message, 7500);",
    "    toast('PSA refresh failed', errorMessage(error, 'PSA refresh failed for an unknown reason.'), 12000);",
    1,
)

checks = {
    "credential save fix": "() => savePsaToken(false)",
    "error normalization": "function errorMessage(error, fallback = 'Unexpected error.')",
    "CardGrade parser": "const cardGrade = String(pickPsaValue(entries, ['cardGrade'",
}
for label, marker in checks.items():
    if marker not in frontend:
        raise RuntimeError(f"{label} was not applied")
if "addEventListener('click', savePsaToken)" in frontend:
    raise RuntimeError("unsafe PSA save binding remains")
if "status.textContent = error.message;" in frontend:
    raise RuntimeError("plain-string PSA errors are still discarded")
if "normalize_psa_token" not in backend:
    raise RuntimeError("PSA token normalization was not applied")
if "native-tls" not in cargo:
    raise RuntimeError("Windows native TLS was not applied")
frontend_path.write_text(frontend, encoding="utf-8", newline="\n")
