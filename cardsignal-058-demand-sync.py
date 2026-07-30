from pathlib import Path
import json, os

root = Path(os.environ['SOURCE_DIR'])

def once(text, old, new, label):
    if old not in text:
        raise RuntimeError(f'{label} marker not found')
    return text.replace(old, new, 1)

backend_path = root / 'src-tauri/src/lib.rs'
backend = backend_path.read_text(encoding='utf-8').replace('\r\n', '\n')
helper = r'''fn demand_status_is_transient(status: reqwest::StatusCode) -> bool {
    matches!(status.as_u16(), 408 | 425 | 429) || status.is_server_error()
}

async fn demand_fetch_text(
    client: &reqwest::Client,
    url: &str,
) -> Result<(reqwest::StatusCode, String, String, usize), String> {
    for attempt in 1..=2usize {
        match client.get(url).send().await {
            Ok(response) => {
                let status = response.status();
                let final_url = response.url().to_string();
                let retry_after = response.headers().get(RETRY_AFTER)
                    .and_then(|value| value.to_str().ok())
                    .and_then(|value| value.trim().parse::<u64>().ok());
                if attempt == 1 && demand_status_is_transient(status) {
                    drop(response);
                    sleep(Duration::from_secs(retry_after.unwrap_or(2).clamp(1, 10))).await;
                    continue;
                }
                let text = response.text().await
                    .map_err(|error| format!("response body could not be read: {error}"))?;
                return Ok((status, final_url, text, attempt));
            }
            Err(_) if attempt == 1 => sleep(Duration::from_secs(2)).await,
            Err(error) => return Err(format!("request failed after {attempt} attempts: {error}")),
        }
    }
    Err("request failed after 2 attempts".to_string())
}

'''
backend = once(backend, 'async fn download_tcg_reports(state: &AppState, months: u32) -> CsResult<Value> {', helper + 'async fn download_tcg_reports(state: &AppState, months: u32) -> CsResult<Value> {', 'demand retry helper')
old_article = '''        let article_response = match state.http.get(&article_url).send().await {
            Ok(response) => response,
            Err(error) => {
                errors.push(format!("{}: {error}", month_period(date)));
                continue;
            }
        };
        if !article_response.status().is_success() {
            errors.push(format!("{}: article returned HTTP {}.", month_period(date), article_response.status().as_u16()));
            continue;
        }
        let html = article_response.text().await?;'''
new_article = '''        let (article_status, _, html, article_attempts) = match demand_fetch_text(&state.http, &article_url).await {
            Ok(result) => result,
            Err(error) => {
                errors.push(format!("{}: article {error}.", month_period(date)));
                continue;
            }
        };
        if !article_status.is_success() {
            errors.push(format!("{}: article returned HTTP {} after {article_attempts} attempt(s).", month_period(date), article_status.as_u16()));
            continue;
        }'''
backend = once(backend, old_article, new_article, 'article retry')
old_download = '''        let mut found_for_month = 0usize;
        for candidate in candidates {
            let response = match state.http.get(&candidate).send().await {
                Ok(response) => response,
                Err(_) => continue,
            };
            if !response.status().is_success() {
                continue;
            }
            let final_url = response.url().to_string();
            let csv_text = match response.text().await {
                Ok(text) => text,
                Err(_) => continue,
            };
            if !report_pattern.is_match(&csv_text) {
                continue;
            }
            for report in normalize_report_csv(&csv_text, date, &article_url, &final_url)? {
                let id = report.get("id").and_then(Value::as_str).unwrap_or_default().to_string();
                if seen_report_ids.insert(id) {
                    reports.push(report);
                    found_for_month += 1;
                }
            }
        }
        if found_for_month == 0 {
            errors.push(format!("{}: no downloadable MTG report CSVs were found.", month_period(date)));
        }'''
new_download = '''        let mut found_for_month = 0usize;
        let mut download_issues = Vec::<String>::new();
        if candidates.is_empty() {
            download_issues.push("article contained no downloadable CSV links".to_string());
        }
        for (candidate_index, candidate) in candidates.iter().enumerate() {
            let number = candidate_index + 1;
            let (status, final_url, csv_text, attempts) = match demand_fetch_text(&state.http, candidate).await {
                Ok(result) => result,
                Err(error) => {
                    download_issues.push(format!("download {number} {error}"));
                    continue;
                }
            };
            if !status.is_success() {
                download_issues.push(format!("download {number} returned HTTP {} after {attempts} attempt(s)", status.as_u16()));
                continue;
            }
            if !report_pattern.is_match(&csv_text) {
                download_issues.push(format!("download {number} was not a recognizable TCGplayer CSV"));
                continue;
            }
            let normalized = match normalize_report_csv(&csv_text, date, &article_url, &final_url) {
                Ok(value) => value,
                Err(error) => {
                    download_issues.push(format!("download {number} could not be parsed: {error}"));
                    continue;
                }
            };
            for report in normalized {
                let id = report.get("id").and_then(Value::as_str).unwrap_or_default().to_string();
                if seen_report_ids.insert(id) {
                    reports.push(report);
                    found_for_month += 1;
                }
            }
        }
        if found_for_month < 2 {
            let detail = if download_issues.is_empty() { "no usable report rows were found".to_string() } else { download_issues.join("; ") };
            errors.push(format!("{}: retrieved {found_for_month} of 2 report files; {detail}.", month_period(date)));
        }'''
backend = once(backend, old_download, new_download, 'download retry and diagnostics')
backend = backend.replace('const APP_VERSION: &str = "0.5.7";', 'const APP_VERSION: &str = "0.5.8";', 1)
backend_path.write_text(backend, encoding='utf-8', newline='\n')

front_path = root / 'dist/src/app.js'
front = front_path.read_text(encoding='utf-8').replace('\r\n', '\n')
old_result = '''    const rows = reports.reduce((sum, report) => sum + (report.rows?.length || 0), 0);
    const warning = payload.errors?.length ? ` ${payload.errors.length} month(s) could not be retrieved.` : '';
    state.settings.lastDemandSync = new Date().toISOString();
    saveSettings();
    if (!silent) toast('Demand synced', `${reports.length} report lists and ${rows} ranked cards updated.${warning}`, 6500);'''
new_result = '''    const rows = reports.reduce((sum, report) => sum + (report.rows?.length || 0), 0);
    const errors = Array.isArray(payload.errors) ? payload.errors.map((item) => String(item || '').trim()).filter(Boolean) : [];
    state.settings.lastDemandSync = new Date().toISOString();
    state.settings.lastDemandSyncErrors = errors;
    saveSettings();
    if (errors.length) {
      const visible = errors.slice(0, 2).join(' ');
      const more = errors.length > 2 ? ` Plus ${errors.length - 2} more issue(s); see Imports.` : '';
      if (!silent) toast('Demand sync partially completed', `${reports.length} report lists and ${rows} ranked cards updated. ${visible}${more}`, 12000);
      else console.warn('Automatic demand sync partially completed:', errors);
    } else if (!silent) toast('Demand synced', `${reports.length} report lists and ${rows} ranked cards updated.`, 6500);'''
front = once(front, old_result, new_result, 'honest partial-sync result')
front = once(front,
'''function importsView() {
  const lastSync = state.reports.map((report) => Date.parse(report.importedAt)).filter(Number.isFinite).sort((a, b) => b - a)[0];
  return `''',
'''function importsView() {
  const lastSync = state.reports.map((report) => Date.parse(report.importedAt)).filter(Number.isFinite).sort((a, b) => b - a)[0];
  const lastDemandErrors = Array.isArray(state.settings.lastDemandSyncErrors) ? state.settings.lastDemandSyncErrors : [];
  const demandSyncNote = lastDemandErrors.length ? `Last sync was partial: ${escapeHtml(lastDemandErrors.join(' '))}` : (lastSync ? `Last report sync: ${new Date(lastSync).toLocaleString()}` : 'No automatic demand sync has completed yet.');
  return `''', 'imports diagnostics')
front = once(front, "<p class=\"mini-note\">${lastSync ? `Last report sync: ${new Date(lastSync).toLocaleString()}` : 'No automatic demand sync has completed yet.'}</p>", "<p class=\"mini-note\">${demandSyncNote}</p>", 'imports note')
front = once(front,
'''async function maybeAutoSyncDemand() {
  const last = Date.parse(state.settings.lastDemandSync || '');
  const stale = !Number.isFinite(last) || Date.now() - last > 7 * 24 * 60 * 60 * 1000;
  if (!state.reports.length || stale) await syncDemand({ silent: true });
}''',
'''async function maybeAutoSyncDemand() {
  const last = Date.parse(state.settings.lastDemandSync || '');
  const hadErrors = Array.isArray(state.settings.lastDemandSyncErrors) && state.settings.lastDemandSyncErrors.length > 0;
  const retryWindow = hadErrors ? 24 * 60 * 60 * 1000 : 7 * 24 * 60 * 60 * 1000;
  const stale = !Number.isFinite(last) || Date.now() - last > retryWindow;
  if (!state.reports.length || stale) await syncDemand({ silent: true });
}''', 'partial retry window')
front = front.replace('0.5.7', '0.5.8')
front_path.write_text(front, encoding='utf-8', newline='\n')

cargo = root / 'src-tauri/Cargo.toml'
cargo.write_text(cargo.read_text(encoding='utf-8').replace('version = "0.5.7"', 'version = "0.5.8"', 1), encoding='utf-8', newline='\n')
config = root / 'src-tauri/tauri.conf.json'
data = json.loads(config.read_text(encoding='utf-8')); data['version'] = '0.5.8'; config.write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
for rel in ['package.json','README.md','CHANGELOG.md','dist/index.html']:
    path = root / rel
    if path.exists(): path.write_text(path.read_text(encoding='utf-8').replace('0.5.7','0.5.8'), encoding='utf-8', newline='\n')

for marker in ['demand_fetch_text(', 'Demand sync partially completed', 'lastDemandSyncErrors', 'const APP_VERSION: &str = "0.5.8";']:
    if marker not in backend and marker not in front: raise RuntimeError(f'missing validation marker: {marker}')
print('Applied CardSignal 0.5.8 demand sync retry and exact diagnostics.')
