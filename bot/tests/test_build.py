"""Compilation automatique quand version.txt change."""
import asyncio
import os
import time

import pytest

from mt5 import build as b
from mt5.deploy import detect_new_build, plan_deploy

CORE = '﻿// SX_Core\r\n#define SX_VERSION   "5.0"\r\nint x;\r\n'
MQ5 = ('﻿#property copyright   "ScalpX"\r\n#property version     "5.00"\r\n'
       '#property description "ScalpX v5.0 — EMA 10/20"\r\n#include "SX_Core.mqh"\r\n')


def prepare(cfg, txt="5.0", age=60):
    ex = cfg.paths.base / "MQL5" / "Experts"
    (ex / "SX_Core.mqh").write_bytes(CORE.encode("utf-8"))
    (ex / "ScalpX_v5.mq5").write_bytes(MQ5.encode("utf-8"))
    (cfg.paths.base / "MetaEditor64.exe").write_bytes(b"MZ")
    old = time.time() - 600
    for f in (ex / "SX_Core.mqh", ex / "ScalpX_v5.mq5", ex / "ScalpX_v5.ex5"):
        os.utime(f, (old, old))
    v = cfg.paths.base / "version.txt"
    v.write_text(txt)
    t = time.time() - age
    os.utime(v, (t, t))


def fake_metaeditor(cfg, errors=0):
    def run(cmd, timeout):
        assert "/compile:" in cmd and "ScalpX_v5.mq5" in cmd and "/log:" in cmd
        logp = cmd.split('/log:"')[1].rstrip('"')
        res = f"Result: {errors} errors, 1 warnings, 120 msec elapsed"
        if errors:
            res = "SX_Core.mqh(3,1) : error 123: syntax error\r\n" + res
        with open(logp, "wb") as f:
            f.write(res.encode("utf-16"))
        if not errors:
            ver = b.source_version(cfg)
            (cfg.paths.base / "MQL5" / "Experts" / "ScalpX_v5.ex5").write_text("compiled " + ver)
    return run


def test_pas_de_compilation_si_version_identique_ou_trop_recente(cfg, db):
    prepare(cfg, "5.0")
    assert b.pending_build(cfg, db) is None                      # déjà à jour
    prepare(cfg, "5.2", age=1)
    assert b.pending_build(cfg, db) is None                      # fichier en cours d'écriture
    prepare(cfg, "abc")
    assert b.pending_build(cfg, db) is None                      # version invalide


def test_sources_plus_recentes_que_version_txt_non_ecrasees(cfg, db):
    prepare(cfg, "5.2")
    core = cfg.paths.base / "MQL5" / "Experts" / "SX_Core.mqh"
    os.utime(core, None)                                         # SX_VERSION changé à la main après version.txt
    assert b.pending_build(cfg, db) is None


@pytest.mark.asyncio
async def test_compilation_auto_met_la_version_et_compile(cfg, db):
    prepare(cfg, "5.2")
    req = b.pending_build(cfg, db)
    assert req and req.version == "5.2"
    res = await b.auto_build(cfg, db, req, runner=fake_metaeditor(cfg))
    assert res.ok and res.errors == 0 and res.warnings == 1
    ex = cfg.paths.base / "MQL5" / "Experts"
    core = (ex / "SX_Core.mqh").read_bytes()
    mq5 = (ex / "ScalpX_v5.mq5").read_bytes()
    assert core.startswith(b"\xef\xbb\xbf") and b'#define SX_VERSION   "5.2"\r\n' in core      # BOM et CRLF conservés
    assert b'#property version     "5.02"' in mq5 and b"ScalpX v5.2 " in mq5
    assert b.pending_build(cfg, db) is None                      # pas de nouvelle tentative
    assert (ex / "ScalpX_v5.ex5").read_text() == "compiled 5.2"


@pytest.mark.asyncio
async def test_compilation_en_erreur_une_seule_tentative(cfg, db):
    prepare(cfg, "5.3")
    req = b.pending_build(cfg, db)
    res = await b.auto_build(cfg, db, req, runner=fake_metaeditor(cfg, errors=1))
    assert not res.ok and res.errors == 1 and "syntax error" in res.detail
    assert (cfg.paths.base / "MQL5" / "Experts" / "ScalpX_v5.ex5").read_text() == "5.0"   # .ex5 inchangé
    assert b.pending_build(cfg, db) is None


def test_prop_version():
    assert b.prop_version("5.3.1") == "5.03" and b.prop_version("5.12.0") == "5.12" and b.prop_version("6") == "6.00"


@pytest.mark.asyncio
async def test_mise_a_jour_github_meme_version_compile_quand_meme(cfg, db):
    """Sources livrées par GitHub avec SX_VERSION déjà à jour : le .ex5 plus ancien que les sources est recompilé."""
    prepare(cfg, "5.0")
    ex = cfg.paths.base / "MQL5" / "Experts"
    os.utime(ex / "SX_Core.mqh", None)                           # sources copiées par la mise à jour
    v = cfg.paths.base / "version.txt"
    t = time.time() - 30
    os.utime(v, (t, t))
    req = b.pending_build(cfg, db)
    assert req and req.version == "5.0"
    res = await b.auto_build(cfg, db, req, runner=fake_metaeditor(cfg))
    assert res.ok and (ex / "ScalpX_v5.ex5").read_text() == "compiled 5.0"
    assert b.pending_build(cfg, db) is None                      # .ex5 à jour : rien à refaire
