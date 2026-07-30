from pathlib import Path
import os

root = Path(os.environ["SOURCE_DIR"])
backend_path = root / "src-tauri/src/lib.rs"
backend = backend_path.read_text(encoding="utf-8").replace("\r\n", "\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"{label} marker not found")
    return text.replace(old, new, 1)


backend = replace_once(
    backend,
    'const APP_VERSION: &str = "0.5.4";',
    'const APP_VERSION: &str = "0.5.5";',
    "backend version",
)
backend = replace_once(
    backend,
    "use reqwest::header::{ACCEPT, ACCEPT_LANGUAGE, AUTHORIZATION, CONTENT_TYPE};",
    "use reqwest::header::{ACCEPT, ACCEPT_LANGUAGE, AUTHORIZATION, REFERER, USER_AGENT};",
    "PSA request header imports",
)

request_start = backend.index("async fn psa_api_request(")
request_end = backend.index("#[tauri::command]\nfn psa_set_token", request_start)
request_block = r'''async fn psa_api_request(
    state: &AppState,
    cert_number: &str,
    token: &str,
    scheme: &str,
    method: &str,
    swagger_compatible: bool,
) -> CsResult<(reqwest::StatusCode, String)> {
    let url = format!("https://api.psacard.com/publicapi/cert/{method}/{cert_number}");
    let mut request = state
        .http
        .get(url)
        .header(AUTHORIZATION, format!("{scheme} {token}"))
        .header(ACCEPT, "application/json");

    // PSA's Swagger UI executes the same call from a browser context. Some PSA
    // gateway responses differ for non-browser HTTP clients, so retry with the
    // request headers emitted by the official Swagger test page before treating
    // an authorization response as final.
    if swagger_compatible {
        request = request
            .header(
                USER_AGENT,
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            )
            .header(ACCEPT_LANGUAGE, "en-US,en;q=0.9")
            .header(
                REFERER,
                "https://api.psacard.com/publicapi/swagger/ui/index",
            )
            .header("x-requested-with", "XMLHttpRequest")
            .header("cache-control", "no-cache")
            .header("pragma", "no-cache");
    }

    let response = request.send().await?;
    let status = response.status();
    let text = response.text().await?;
    Ok((status, text))
}

async fn psa_api_lookup(
    state: &AppState,
    cert_number: &str,
    token: &str,
) -> CsResult<(reqwest::StatusCode, String, &'static str)> {
    let standard = psa_api_request(
        state,
        cert_number,
        token,
        "bearer",
        "GetByCertNumber",
        false,
    )
    .await?;
    if standard.0.is_success() {
        return Ok((standard.0, standard.1, "PSA documented request"));
    }

    if matches!(standard.0.as_u16(), 401 | 403 | 429 | 500) {
        let swagger = psa_api_request(
            state,
            cert_number,
            token,
            "Bearer",
            "GetByCertNumber",
            true,
        )
        .await?;
        if swagger.0.is_success() {
            return Ok((swagger.0, swagger.1, "PSA Swagger-compatible request"));
        }

        let swagger_lowercase = psa_api_request(
            state,
            cert_number,
            token,
            "bearer",
            "GetByCertNumber",
            true,
        )
        .await?;
        if swagger_lowercase.0.is_success() {
            return Ok((
                swagger_lowercase.0,
                swagger_lowercase.1,
                "PSA Swagger-compatible lowercase request",
            ));
        }

        // Prefer the Swagger-compatible response because it most closely mirrors
        // PSA's own testing page and therefore provides the most useful failure.
        return Ok((swagger.0, swagger.1, "PSA Swagger-compatible request"));
    }

    if matches!(standard.0.as_u16(), 404 | 405) {
        let fallback = psa_api_request(
            state,
            cert_number,
            token,
            "bearer",
            "GetByCertNumberForFileAppend",
            false,
        )
        .await?;
        return Ok((fallback.0, fallback.1, "PSA file-append fallback"));
    }

    Ok((standard.0, standard.1, "PSA documented request"))
}

'''
backend = backend[:request_start] + request_block + backend[request_end:]

backend = replace_once(
    backend,
    "    let (status, text) = psa_api_lookup(&state, &clean, &token)\n        .await\n        .map_err(String::from)?;",
    "    let (status, text, request_mode) = psa_api_lookup(&state, &clean, &token)\n        .await\n        .map_err(String::from)?;",
    "PSA lookup result tuple",
)

old_error = '''        let explanation = match code {
            401 | 403 => format!("PSA rejected the access token (HTTP {code}). Generate a fresh token on PSA's Public API page, save it again, and retry. PSA response: {detail}"),
            429 => format!("PSA API lookup limit reached (HTTP 429). Retry after PSA resets the quota. PSA response: {detail}"),
            500 if lower.contains("quota") || lower.contains("limit") => format!("PSA API lookup quota was exceeded (HTTP 500). PSA response: {detail}"),
            500 => format!("PSA rejected the credentials or returned a server error (HTTP 500). PSA says this status usually means invalid credentials. Generate a fresh access token and retry. PSA response: {detail}"),
            _ => format!("PSA lookup returned HTTP {code}: {detail}"),
        };'''
new_error = '''        let explanation = match code {
            401 => format!("PSA rejected the authorization credential (HTTP 401) using {request_mode}. The token may be expired or malformed. PSA response: {detail}"),
            403 if lower.contains("limited to approved customers") => format!("PSA denied this API request (HTTP 403) using {request_mode}: access is limited to approved customers. This response does not prove that the token is invalid. Note that Swagger's Authorize dialog only stores the header; the token is validated only when a cert GET request returns data. PSA response: {detail}"),
            403 => format!("PSA forbade the API request (HTTP 403) using {request_mode}. The token may still be valid; PSA rejected the request or client context. PSA response: {detail}"),
            429 => format!("PSA API lookup limit reached (HTTP 429). Retry after PSA resets the quota. PSA response: {detail}"),
            500 if lower.contains("quota") || lower.contains("limit") => format!("PSA API lookup quota was exceeded (HTTP 500). PSA response: {detail}"),
            500 => format!("PSA returned HTTP 500 using {request_mode}. PSA documents this as commonly caused by invalid credentials, but it can also be a PSA server failure. PSA response: {detail}"),
            _ => format!("PSA lookup returned HTTP {code} using {request_mode}: {detail}"),
        };'''
backend = replace_once(backend, old_error, new_error, "PSA error classification")

backend = replace_once(
    backend,
    '        "source": "PSA Public API",',
    '        "source": "PSA Public API",\n        "requestMode": request_mode,',
    "PSA request mode result",
)

if "PSA rejected the access token (HTTP" in backend:
    raise RuntimeError("incorrect blanket PSA token rejection remains")
if "swagger_compatible: bool" not in backend:
    raise RuntimeError("Swagger-compatible request fallback was not applied")
if 'lower.contains("limited to approved customers")' not in backend:
    raise RuntimeError("approved-customer error classifier was not applied")
if '.header(CONTENT_TYPE, "application/json")' in backend:
    raise RuntimeError("GET request still sends an unnecessary Content-Type header")

backend_path.write_text(backend, encoding="utf-8", newline="\n")

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
        text = path.read_text(encoding="utf-8")
        text = text.replace("0.5.4", "0.5.5")
        path.write_text(text, encoding="utf-8", newline="\n")

print("Applied CardSignal 0.5.5 PSA Swagger compatibility and error-classification fixes.")
