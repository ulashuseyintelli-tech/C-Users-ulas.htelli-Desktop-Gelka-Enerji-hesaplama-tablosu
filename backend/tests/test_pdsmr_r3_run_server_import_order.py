"""
PDSMR-R3 STEP 3 — run_server.py'nin KAYNAK SIRASI kaniti.

Gercek frozen exe'nin calisma-zamani davranisini AYRICA disposable
paketlenmis matriste (bkz. PDSMR-R3 kapanis raporu) kanitliyoruz; bu test
KAYNAK KODUN kendisinin, herhangi bir ORM/router yan etkisinden (app.main
import'u) ONCE sema kapisini cagiracak SEKILDE yazildigini statik olarak
dogrular - refactor sirasinda sira YANLISLIKLA bozulursa hemen yakalanir.
"""
from __future__ import annotations

import ast
import os


def _run_server_source() -> str:
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(backend_dir, "run_server.py"), encoding="utf-8") as fh:
        return fh.read()


def test_gate_call_precedes_app_main_import_in_source_order():
    kaynak = _run_server_source()
    agac = ast.parse(kaynak)

    main_fonksiyonu = next(
        node for node in agac.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )

    gate_cagri_satiri = None
    import_satiri = None
    for node in ast.walk(main_fonksiyonu):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_run_startup_schema_gate":
                gate_cagri_satiri = node.lineno
        if isinstance(node, ast.ImportFrom) and node.module == "app.main":
            import_satiri = node.lineno

    assert gate_cagri_satiri is not None, "main() icinde _run_startup_schema_gate() cagrisi YOK"
    assert import_satiri is not None, "main() icinde 'from app.main import app' YOK"
    assert gate_cagri_satiri < import_satiri, (
        f"_run_startup_schema_gate() (satir {gate_cagri_satiri}) "
        f"'from app.main import app' (satir {import_satiri}) SATIRINDAN "
        "SONRA cagriliyor - STEP 3 ihlali"
    )


def test_gate_only_runs_when_frozen():
    """Dev ortaminda (frozen DEGIL) sema kapisi calismamali - owner:
    'Development/test create_all behavior may remain... explicitly
    isolated from packaged mode'."""
    kaynak = _run_server_source()
    agac = ast.parse(kaynak)

    main_fonksiyonu = next(
        node for node in agac.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    ilk_deyim = main_fonksiyonu.body[0]
    # ilk deyim argparse kurulumu; gate cagrisi bir `if getattr(sys,
    # 'frozen', False):` blogunun ICINDE olmali.
    frozen_kontrolu_var = False
    for node in ast.walk(main_fonksiyonu):
        if isinstance(node, ast.If):
            kaynak_parcasi = ast.get_source_segment(kaynak, node.test) or ""
            if "frozen" in kaynak_parcasi:
                govde_kaynagi = "\n".join(
                    ast.get_source_segment(kaynak, s) or "" for s in node.body
                )
                if "_run_startup_schema_gate" in govde_kaynagi:
                    frozen_kontrolu_var = True
    assert frozen_kontrolu_var, (
        "_run_startup_schema_gate() bir 'if ... frozen ...:' blogu DISINDA "
        "cagriliyor gibi gorunuyor - dev ortaminda da calisir hale gelmis "
        "olabilir (regresyon riski)"
    )


def test_enforce_packaged_environment_invariants_precedes_gate_and_import():
    """PDSMR-R3B STEP 5 — _enforce_packaged_environment_invariants()
    (Electron'dan BAGIMSIZ dogrulama), _run_startup_schema_gate()'DEN VE
    'from app.main import app'DAN ONCE cagrilmali - fail-closed'in EN ERKEN
    noktada (DB'ye/scheme'e HIC dokunmadan) devreye girmesi icin."""
    kaynak = _run_server_source()
    agac = ast.parse(kaynak)

    main_fonksiyonu = next(
        node for node in agac.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )

    invariant_satiri = None
    gate_cagri_satiri = None
    import_satiri = None
    for node in ast.walk(main_fonksiyonu):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_enforce_packaged_environment_invariants":
                invariant_satiri = node.lineno
            if node.func.id == "_run_startup_schema_gate":
                gate_cagri_satiri = node.lineno
        if isinstance(node, ast.ImportFrom) and node.module == "app.main":
            import_satiri = node.lineno

    assert invariant_satiri is not None, "main() icinde _enforce_packaged_environment_invariants() cagrisi YOK"
    assert gate_cagri_satiri is not None
    assert import_satiri is not None
    assert invariant_satiri < gate_cagri_satiri, (
        f"_enforce_packaged_environment_invariants() (satir {invariant_satiri}) "
        f"_run_startup_schema_gate() (satir {gate_cagri_satiri}) SATIRINDAN "
        "SONRA cagriliyor - STEP 5 ihlali (env dogrulamasi DB dogrulamasindan ONCE olmali)"
    )
    assert invariant_satiri < import_satiri


def _load_run_server_module():
    import importlib.util
    import sys as _sys

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _sys.path.insert(0, backend_dir)
    spec = importlib.util.spec_from_file_location(
        "run_server_test_import2", os.path.join(backend_dir, "run_server.py")
    )
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_enforce_packaged_environment_invariants_passes_when_both_correct(monkeypatch):
    modul = _load_run_server_module()
    monkeypatch.setenv("ENV", "desktop")
    monkeypatch.setenv("GELKA_PACKAGED_RUNTIME", "1")
    modul._enforce_packaged_environment_invariants()  # istisna FIRLATMAMALI


def test_enforce_packaged_environment_invariants_hard_stops_when_env_missing(monkeypatch):
    modul = _load_run_server_module()
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("GELKA_PACKAGED_RUNTIME", "1")
    try:
        modul._enforce_packaged_environment_invariants()
        assert False, "SystemExit BEKLENIYORDU"
    except SystemExit as exc:
        assert exc.code == 60


def test_enforce_packaged_environment_invariants_hard_stops_when_env_is_stale_staging(monkeypatch):
    """PDSMR-R3B'DEN ONCEKI 'staging' degeri ARTIK GECERSIZ - eski bir
    machine-local.env/kalinti ortam degiskeni bunu ASLA gecirmemeli."""
    modul = _load_run_server_module()
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("GELKA_PACKAGED_RUNTIME", "1")
    try:
        modul._enforce_packaged_environment_invariants()
        assert False, "SystemExit BEKLENIYORDU"
    except SystemExit as exc:
        assert exc.code == 60


def test_enforce_packaged_environment_invariants_hard_stops_when_packaged_runtime_missing(monkeypatch):
    modul = _load_run_server_module()
    monkeypatch.setenv("ENV", "desktop")
    monkeypatch.delenv("GELKA_PACKAGED_RUNTIME", raising=False)
    try:
        modul._enforce_packaged_environment_invariants()
        assert False, "SystemExit BEKLENIYORDU"
    except SystemExit as exc:
        assert exc.code == 60


def test_database_url_to_path_round_trips_with_db_routing_js_convention():
    """
    backend/run_server.py::_database_url_to_path() ile electron/dbRouting.js::
    toSqliteUrl() AYNI sozlesmeyi (RAW, URI-encode YOK) paylasmali - biri
    olusturur, digeri cozer, ikisi de AYNI formul olmali.
    """
    import sys

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, backend_dir)
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_server_test_import", os.path.join(backend_dir, "run_server.py")
    )
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)

    ornekler = [
        r"C:\Users\ulastelli\AppData\Roaming\gelka-enerji\database\gelka_enerji.db",
        r"C:\Program Files\Gelka Enerji\resources\backend\gelka_enerji.db",
    ]
    for dosya_yolu in ornekler:
        url = "sqlite:///" + dosya_yolu.replace("\\", "/")
        geri = modul._database_url_to_path(url)
        assert geri == dosya_yolu, f"round-trip basarisiz: {dosya_yolu!r} -> {url!r} -> {geri!r}"
