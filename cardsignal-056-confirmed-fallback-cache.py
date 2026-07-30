from pathlib import Path
import os

root = Path(os.environ['SOURCE_DIR'])


def replace_once(text, old, new, label):
    if old not in text:
        raise RuntimeError(f'{label} marker not found')
    return text.replace(old, new, 1)

backend_path = root / 'src-tauri/src/lib.rs'
backend = backend_path.read_text(encoding='utf-8').replace('\r\n', '\n')
backend = replace_once(
    backend,
    """async fn psa_public_page_lookup(number: String, state: State<'_, AppState>) -> Result<Value, String> {\n    let clean = clean_psa_number(&number).map_err(String::from)?;\n    if let Some(payload) = psa_cache_get(&state.database_path, &clean).map_err(String::from)? {\n        return Ok(payload);\n    }\n\n    let source_url""",
    """async fn psa_public_page_lookup(number: String, state: State<'_, AppState>) -> Result<Value, String> {\n    let clean = clean_psa_number(&number).map_err(String::from)?;\n    let source_url""",
    'public preview cache read',
)
backend = replace_once(
    backend,
    """    psa_cache_put(&state.database_path, &clean, &payload).map_err(String::from)?;\n    Ok(payload)\n}\n\n#[tauri::command]\nasync fn updater_check""",
    """    // Public-page results remain a preview until the user accepts them in the UI.\n    Ok(payload)\n}\n\n#[tauri::command]\nasync fn updater_check""",
    'public preview cache write',
)
backend_path.write_text(backend, encoding='utf-8', newline='\n')

frontend_path = root / 'dist/src/app.js'
frontend = frontend_path.read_text(encoding='utf-8').replace('\r\n', '\n')
frontend = replace_once(
    frontend,
    """function applyPsaFields(slab, only = null) {\n  const values = psaFormFields(slab);\n  Object.entries(values).forEach(([name, value]) => {\n    if (!only || only === name) setSlabFormValue(name, value);\n  });\n}\n\nfunction renderPsaLookupResult""",
    """function applyPsaFields(slab, only = null) {\n  const values = psaFormFields(slab);\n  Object.entries(values).forEach(([name, value]) => {\n    if (!only || only === name) setSlabFormValue(name, value);\n  });\n}\n\nasync function cacheConfirmedPsaPreview() {\n  const lookup = state.psaLookup;\n  if (!lookup?.payload || !lookup?.normalized?.certNumber) return;\n  if (!String(lookup.payload.source || '').includes('public cert page')) return;\n  await put('psaCache', {\n    id: `psa:${lookup.normalized.certNumber}`,\n    certNumber: lookup.normalized.certNumber,\n    cachedAt: new Date().toISOString(),\n    payload: lookup.payload,\n  });\n  await refreshPsaStatus();\n}\n\nfunction renderPsaLookupResult""",
    'confirmed preview cache helper',
)
frontend = replace_once(
    frontend,
    """  $('#apply-all-psa-fields')?.addEventListener('click', () => {\n    applyPsaFields(slab);\n    toast('PSA fields copied', 'Review the form, then add purchase price or estimated value.');\n    $('#slab-form').elements.namedItem('purchasePrice')?.focus();\n  });""",
    """  $('#apply-all-psa-fields')?.addEventListener('click', async () => {\n    applyPsaFields(slab);\n    try { await cacheConfirmedPsaPreview(); } catch (error) { console.error('Could not cache confirmed PSA preview:', error); }\n    toast('PSA fields copied', 'Review the form, then add purchase price or estimated value.');\n    $('#slab-form').elements.namedItem('purchasePrice')?.focus();\n  });""",
    'copy-all cache confirmation',
)
frontend = replace_once(
    frontend,
    """  await put('slabs', slab);\n  closeModals();""",
    """  await put('slabs', slab);\n  if (verifiedLookup) {\n    try { await cacheConfirmedPsaPreview(); } catch (error) { console.error('Could not cache confirmed PSA preview:', error); }\n  }\n  closeModals();""",
    'save slab cache confirmation',
)
frontend_path.write_text(frontend, encoding='utf-8', newline='\n')

if 'cacheConfirmedPsaPreview' not in frontend:
    raise RuntimeError('confirmed fallback cache helper missing')
if 'Public-page results remain a preview' not in backend:
    raise RuntimeError('public fallback still caches before confirmation')
print('Applied public-page confirmation cache fix.')
