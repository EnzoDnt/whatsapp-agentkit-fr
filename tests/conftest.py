"""
Socle des tests d'intégration.

Chaque test part d'une base vierge et d'une application réellement montée : on
frappe les routes HTTP, pas les fonctions. Un test qui appelle directement la
fonction ne vérifie ni le routage, ni les dépendances, ni l'authentification —
c'est-à-dire précisément là où les régressions se logent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))


@pytest.fixture()
def env_propre(tmp_path, monkeypatch):
    """Environnement isolé : base neuve, dossiers neufs, variables maîtrisées."""
    (tmp_path / "config").mkdir()
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "simulateur").mkdir()
    # La console est servie depuis simulateur/ : on copie les pages réelles.
    for page in ("admin.html", "index.html"):
        source = RACINE / "simulateur" / page
        if source.exists():
            (tmp_path / "simulateur" / page).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
    (tmp_path / "config" / "prompts.yaml").write_text(
        'system_prompt: "Tu es un assistant de test."\n'
        'fallback_message: "Reformulez ?"\n'
        'error_message: "Panne."\n'
        'quota_message: "Quota atteint."\n',
        encoding="utf-8",
    )
    (tmp_path / "config" / "entreprise.yaml").write_text(
        "delais_heures:\n  ceremonie: 72\n  standard: 0\n", encoding="utf-8"
    )
    (tmp_path / "knowledge" / "tarifs.md").write_text(
        "# Tarifs\n- Entremets 10 parts : 48 EUR\n- Baguette : 1,40 EUR\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    variables = {
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/test.db",
        "ENVIRONMENT": "development",
        "ADMIN_TOKEN": "jeton-installation-de-test",
        "SESSION_SECRET": "secret-de-session-de-test",
        "WHATSAPP_PROVIDER": "simulateur",
        "META_APP_SECRET": "secret-webhook-de-test",
        "META_ACCESS_TOKEN": "faux",
        "META_PHONE_NUMBER_ID": "faux",
        "SIMULATEUR_SECRET": "secret-webhook-de-test",
        "PII_HASH_SALT": "sel-de-test",
        "ANTHROPIC_API_KEY": "sk-ant-faux",
        "LLM_PROVIDER": "anthropic",
        "TEST_ALLOWLIST": "",
        "RATE_LIMIT_MESSAGES": "20",
        "PLAFOND_DEPENSE_JOUR": "5",
        "MODE_TRANSPARENCE": "discrete",
        "LOG_MESSAGE_CONTENT": "false",
    }
    for cle, valeur in variables.items():
        monkeypatch.setenv(cle, valeur)

    # Les modules lisent leur configuration à l'import : on les recharge tous,
    # dans l'ordre des dépendances, pour que chaque test reparte propre.
    import importlib

    for nom in (
        "agent.securite", "agent.llm", "agent.memory", "agent.auth", "agent.tools",
        "agent.brain", "agent.providers.base", "agent.providers.meta",
        "agent.providers.simulateur", "agent.providers", "agent.admin", "agent.main",
    ):
        if nom in sys.modules:
            importlib.reload(sys.modules[nom])
        else:
            importlib.import_module(nom)

    yield tmp_path

    # Le moteur SQLAlchemy garde un fil aiosqlite : sans fermeture explicite,
    # il survit à la boucle du test et remonte des « Event loop is closed ».
    import asyncio
    import contextlib

    with contextlib.suppress(Exception):
        moteur = sys.modules["agent.memory"].moteur
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(moteur.dispose())


@pytest.fixture()
def app(env_propre):
    import sys as _s

    return _s.modules["agent.main"].app


@pytest.fixture()
def client(app):
    """Client HTTP branché sur l'application, cycle de vie compris."""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def connecte(client):
    """Client authentifié : premier compte créé puis session ouverte."""
    r = client.post("/admin/amorcer", json={
        "nom": "Testeuse", "email": "test@exemple.fr",
        "mot_de_passe": "mot-de-passe-de-test", "jeton": "jeton-installation-de-test",
    })
    assert r.status_code == 200, r.text
    return client


@pytest.fixture()
def cerveau_simule(monkeypatch):
    """Remplace l'appel au modèle : les tests ne dépendent pas du réseau."""
    async def faux(message, historique, telephone=""):
        return (f"[réponse à {message!r}]", True)

    import sys as _s

    monkeypatch.setattr(_s.modules["agent.main"], "generer_reponse", faux)
    return faux
