from pathlib import Path
import json
import os
import re

root = Path(os.environ['SOURCE_DIR'])


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f'{label} marker not found')
    return text.replace(old, new, 1)


# Rust backend
backend_path = root / 'src-tauri/src/lib.rs'
backend = backend_path.read_text(encoding='utf-8').replace('\r\n', '\n')

backend = replace_once(
    backend,
    'use tauri_plugin_updater::UpdaterExt;',
    'use tauri_plugin_updater::UpdaterExt;\nuse tokio::{sync::Mutex as AsyncMutex, time::{sleep, Instant as TokioInstant}};',
    'tokio request-gate imports',
)
backend = replace_once(
    backend,
    'const DATABASE_NAME: &str = "cardsignal.db";',
    'const DATABASE_NAME: &str = "cardsignal.db";\nconst PSA_API_MIN_INTERVAL: Duration = Duration::from_secs(3);',
    'PSA request interval constant',
)
backend = replace_once(
    backend,
    '    ebay_token: Mutex<Option<CachedEbayToken>>,\n    started_at: DateTime<Utc>,',
    '    ebay_token: Mutex<Option<CachedEbayToken>>,\n    psa_request_gate: AsyncMutex<Option<TokioInstant>>,\n    started_at: DateTime<Utc>,',
    'AppState PSA gate field',
)

init_pattern = re.compile(r'(?P<indent>\s*)ebay_token:\s*Mutex::new\(None\),(?P<after>\s*\n(?P=indent)started_at:)')
match = init_pattern.search(backend)
if not match:
    raise RuntimeError('AppState initialization marker not found')
indent = match.group('indent')
replacement = (
    f'{indent}ebay_token: Mutex::new(None),\n'
    f'{indent}psa_request_gate: AsyncMutex::new(None),'
    f'{match.group("after")}'
)
backend = backend[:match.start()] + replacement + backend[match.end():]

backend = replace_once(
    backend,
    '        "blockedUntil": blocked_until,\n    }))',
    '        "blockedUntil": blocked_until,\n        "minimumIntervalSeconds": PSA_API_MIN_INTERVAL.as_secs(),\n    }))',
    'PSA status interval',
)

old_request = '''    let url = format!("https://api.psacard.com/publicapi/cert/GetByCertNumber/{clean}");
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
    })?;'''
new_request = '''    // Serialize PSA requests and space request starts so double-clicks, overlapping
    // searches, or multiple windows cannot burst PSA's endpoint.
    let mut request_gate = state.psa_request_gate.lock().await;
    let mut request_delay_ms = 0u64;
    if let Some(last_started) = *request_gate {
        let elapsed = last_started.elapsed();
        if elapsed < PSA_API_MIN_INTERVAL {
            let delay = PSA_API_MIN_INTERVAL - elapsed;
            request_delay_ms = delay.as_millis().min(u128::from(u64::MAX)) as u64;
            sleep(delay).await;
        }
    }
    *request_gate = Some(TokioInstant::now());

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
    drop(request_gate);'''
backend = replace_once(backend, old_request, new_request, 'serialized PSA request block')
backend = replace_once(
    backend,
    '        "cached": false,\n        "record": record,',
    '        "cached": false,\n        "requestDelayMs": request_delay_ms,\n        "record": record,',
    'PSA request delay result',
)

# Version after the 0.5.6 patches have run.
backend = backend.replace('const APP_VERSION: &str = "0.5.6";', 'const APP_VERSION: &str = "0.5.7";', 1)
backend_path.write_text(backend, encoding='utf-8', newline='\n')

# Cargo dependency and version
cargo_path = root / 'src-tauri/Cargo.toml'
cargo = cargo_path.read_text(encoding='utf-8')
cargo = cargo.replace('version = "0.5.6"', 'version = "0.5.7"', 1)
if '\ntokio = ' not in cargo:
    cargo = replace_once(
        cargo,
        'tauri-plugin-updater = "2"',
        'tauri-plugin-updater = "2"\ntokio = { version = "1", features = ["sync", "time"] }',
        'tokio dependency',
    )
cargo_path.write_text(cargo, encoding='utf-8', newline='\n')

# Tauri configuration
config_path = root / 'src-tauri/tauri.conf.json'
config = json.loads(config_path.read_text(encoding='utf-8'))
config['version'] = '0.5.7'
config_path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')

# Frontend status and visible version strings
frontend_path = root / 'dist/src/app.js'
frontend = frontend_path.read_text(encoding='utf-8').replace('\r\n', '\n')
frontend = replace_once(
    frontend,
    "  pieces.push(`${formatNumber(payload.cacheCount || 0)} cached cert${Number(payload.cacheCount) === 1 ? '' : 's'}`);",
    "  pieces.push(`${formatNumber(payload.cacheCount || 0)} cached cert${Number(payload.cacheCount) === 1 ? '' : 's'}`);\n  pieces.push(`${formatNumber(payload.minimumIntervalSeconds || 3)}-second API spacing`);",
    'frontend PSA spacing status',
)
frontend = frontend.replace('0.5.6', '0.5.7')
frontend_path.write_text(frontend, encoding='utf-8', newline='\n')

for rel in ['package.json', 'README.md', 'CHANGELOG.md', 'dist/index.html']:
    path = root / rel
    if path.exists():
        path.write_text(path.read_text(encoding='utf-8').replace('0.5.6', '0.5.7'), encoding='utf-8', newline='\n')

checks = {
    'async request gate': 'psa_request_gate: AsyncMutex<Option<TokioInstant>>',
    'serialized request lock': 'let mut request_gate = state.psa_request_gate.lock().await;',
    'minimum request interval': 'const PSA_API_MIN_INTERVAL: Duration = Duration::from_secs(3);',
    'no recursive retry': 'No retry was attempted.',
    'new app version': 'const APP_VERSION: &str = "0.5.7";',
}
for label, marker in checks.items():
    if marker not in backend:
        raise RuntimeError(f'{label} was not applied')
if 'return psa_lookup' in backend or 'psa_lookup(' in backend[backend.index('if !status.is_success()'):backend.index('let record: Value')]:
    raise RuntimeError('automatic PSA retry detected')

print('Applied CardSignal 0.5.7 serialized PSA API request gate.')
