"""Bot 3.20.0 : 📄 création de plusieurs comptes depuis un fichier texte."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

from bot import bulk


def test_lecture_du_fichier():
    rows, errors = bulk.parse("# mes comptes\n\n11110005:mdp:avec:deux:RoboForex-Pro\r\n11110006:abc:RoboForex-ECN\n"
                              "abc:x:Srv\n11110007:x\n11110005:autre:RoboForex-Pro\n11110008::RoboForex-Pro\n")
    assert [(r.login, r.password, r.server) for r in rows] == [("11110005", "mdp:avec:deux", "RoboForex-Pro"),
                                                            ("11110006", "abc", "RoboForex-ECN")]
    assert len(errors) == 4 and not any("mdp" in e or "autre" in e for e in errors)      # jamais de mot de passe
    assert "ligne 5" in errors[0] and "double" in errors[2]


def test_comptes_existants_ignores_et_resume_sans_mot_de_passe():
    rows, errors = bulk.parse("1001:secret1:Srv-A\n1002:secret2:Srv-A\n")
    b = bulk.new_batch(rows, errors, lambda iid: iid.startswith("1001"))
    assert [r.login for r in b.rows] == ["1002"] and b.skipped and "secret" not in bulk.summary(b)


def test_creation_en_parallele_limitee_puis_mots_de_passe_oublies():
    rows, _ = bulk.parse("\n".join(f"{1000 + i}:pw{i}:Srv-A" for i in range(5)))
    b = bulk.new_batch(rows, [], lambda iid: False)
    live, peak, seen, texts = [0], [0], [], []

    async def create(login, pw, server, progress):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        seen.append(pw)
        await progress("🚀 connexion…")
        await asyncio.sleep(0.02)
        live[0] -= 1
        ok = login != "1003"
        return NS(ok=ok, iid=f"{login}_{server}", message="L'EA s'est chargé mais le compte n'est pas connecté")

    async def after_ok(iid):
        return "💾 preset ⭐ Martin appliqué"

    async def edit(t):
        texts.append(t)

    asyncio.run(bulk.run(b, 2, create, after_ok, edit, tick_s=0.001))
    assert peak[0] == 2 and sorted(seen) == [f"pw{i}" for i in range(5)]
    assert all(r.password == "" for r in b.rows)
    assert "4 connecté(s), 1 échec(s)" in texts[-1] and "preset" in texts[-1] and not any("pw" in t for t in texts)
