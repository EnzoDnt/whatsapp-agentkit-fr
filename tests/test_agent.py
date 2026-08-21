"""
Tests du durcissement.

Ils ne vérifient pas seulement que « ça marche » : ils verrouillent les trois
défauts corrigés par rapport au kit d'origine, pour qu'ils ne reviennent pas.
"""

from __future__ import annotations

import importlib
import json
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_agentkit.db")
os.environ.setdefault("PII_HASH_SALT", "sel-de-test")


# ── Masquage des données personnelles ────────────────────────────────────

def test_le_numero_n_apparait_jamais_en_clair():
    from agent.securite import masquer_telephone

    masque = masquer_telephone("33612345678")
    assert "3361234567" not in masque
    assert masque.endswith("78")          # 2 derniers chiffres pour le débogage
    assert masque.startswith("tel_")


def test_le_masquage_est_stable_et_non_reversible():
    from agent.securite import masquer_telephone

    assert masquer_telephone("33612345678") == masquer_telephone("33612345678")
    assert masquer_telephone("33612345678") != masquer_telephone("33612345679")


def test_le_contenu_des_messages_est_masque_par_defaut():
    from agent.securite import masquer_contenu

    resultat = masquer_contenu("Je voudrais commander un gâteau pour samedi")
    assert "gâteau" not in resultat
    assert "caractères" in resultat


# ── Fail-closed sur la signature ─────────────────────────────────────────

def test_production_refuse_de_demarrer_sans_app_secret(monkeypatch):
    """Le défaut n°1 du kit d'origine : accepter tout webhook si le secret est vide."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTORISER_WEBHOOK_NON_SIGNE", "true")  # doit être ignoré
    monkeypatch.setenv("META_APP_SECRET", "")
    monkeypatch.setenv("META_ACCESS_TOKEN", "x")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "y")

    import agent.providers.base as base
    import agent.providers.meta as meta

    importlib.reload(base)
    importlib.reload(meta)

    assert base.autoriser_webhook_non_signe() is False
    with pytest.raises(base.ErreurConfiguration):
        meta.FournisseurMeta()


def test_le_contournement_reste_possible_en_local(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTORISER_WEBHOOK_NON_SIGNE", "true")
    monkeypatch.setenv("META_APP_SECRET", "")

    import agent.providers.base as base

    importlib.reload(base)
    assert base.autoriser_webhook_non_signe() is True


# ── Limitation de débit et plafond de dépense ────────────────────────────

def test_le_limiteur_coupe_au_dela_du_quota():
    from agent.securite import LimiteurDebit

    l = LimiteurDebit(max_messages=3, fenetre_secondes=60)
    assert [l.autoriser("336")[0] for _ in range(5)] == [True, True, True, False, False]


def test_le_plafond_de_depense_declenche():
    from agent.securite import CompteurDepense

    c = CompteurDepense(plafond_journalier=0.05)
    assert c.depassement() is False
    c.enregistrer("claude-sonnet-5", 16_000, 1_200)   # ≈ 0,066 $
    assert c.depassement() is True


# ── Le simulateur produit un vrai webhook Meta ───────────────────────────

def test_le_simulateur_signe_reellement_ses_webhooks():
    from agent.providers.simulateur import SECRET_SIMULATEUR, payload_signe, signer

    corps, entetes = payload_signe("Bonjour")
    assert entetes["X-Hub-Signature-256"] == signer(corps)
    assert entetes["X-Hub-Signature-256"].startswith("sha256=")


def test_le_payload_du_simulateur_a_la_forme_de_meta():
    from agent.providers.simulateur import construire_payload

    p = construire_payload("Bonjour")
    assert p["object"] == "whatsapp_business_account"
    msg = p["entry"][0]["changes"][0]["value"]["messages"][0]
    assert msg["type"] == "text" and msg["text"]["body"] == "Bonjour"
    assert p["entry"][0]["changes"][0]["field"] == "messages"


def test_le_parseur_meta_lit_le_payload_du_simulateur():
    """Preuve que simulateur et production partagent le même code."""
    import asyncio

    from agent.providers.simulateur import construire_payload

    os.environ["META_APP_SECRET"] = "secret"
    os.environ["META_ACCESS_TOKEN"] = "x"
    os.environ["META_PHONE_NUMBER_ID"] = "y"
    import agent.providers.base as base
    import agent.providers.meta as meta

    importlib.reload(base)
    importlib.reload(meta)

    class FausseRequete:
        def __init__(self, d): self._d = d
        async def json(self): return self._d

    messages = asyncio.run(
        meta.FournisseurMeta().parser_webhook(
            FausseRequete(construire_payload("Bonjour", "33612345678"))
        )
    )
    assert len(messages) == 1
    assert messages[0].texte == "Bonjour"
    assert messages[0].telephone == "33612345678"
    assert messages[0].est_sortant is False


# ── Les outils sont réellement exécutables ───────────────────────────────

def test_les_outils_sont_declares_avec_un_schema_valide():
    from agent.tools import schemas_outils

    noms = set()
    for s in schemas_outils():
        assert {"name", "description", "input_schema"} <= set(s)
        assert s["input_schema"]["type"] == "object"
        noms.add(s["name"])
    assert {"rechercher_information", "verifier_delai", "enregistrer_demande"} <= noms


def test_le_delai_est_verifie_en_code(tmp_path, monkeypatch):
    """Le modèle ne doit pas pouvoir accepter une date impossible."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "entreprise.yaml").write_text(
        "delais_heures:\n  ceremonie: 72\n", encoding="utf-8"
    )
    import agent.tools as tools

    importlib.reload(tools)
    from datetime import datetime, timedelta

    demain = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    assert tools.verifier_delai("ceremonie", demain).startswith("REFUSÉ")

    dans_cinq_jours = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
    assert tools.verifier_delai("ceremonie", dans_cinq_jours).startswith("ACCEPTÉ")


def test_un_outil_inconnu_ne_fait_pas_tomber_l_agent():
    from agent.tools import executer_outil

    assert "inconnu" in executer_outil("outil_qui_n_existe_pas", {}).lower()
