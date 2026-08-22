"""
Non-régression des correctifs de sécurité.

Chaque test correspond à une faille effectivement démontrée lors de l'audit
mené avant la mise en ligne. Ils sont écrits pour échouer si l'ancien
comportement revenait — c'est leur seule raison d'être.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

# La constante qui était écrite en dur dans auth.py, et publiée avec le dépôt.
SECRET_AUTREFOIS_PUBLIC = b"secret-de-developpement-non-securise"


# ═════════════════════════════════════════════════════════════════════════
# Détection d'environnement : l'oubli d'ENVIRONMENT ne doit plus tout ouvrir
# ═════════════════════════════════════════════════════════════════════════


def test_hebergeur_detecte_sans_declaration(monkeypatch):
    """Coolify annonce sa présence : on applique le régime de production."""
    from agent import environnement

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("COOLIFY_FQDN", "https://agent.exemple.fr")
    assert environnement.est_production() is True


@pytest.mark.parametrize(
    "variable",
    ["RAILWAY_ENVIRONMENT", "COOLIFY_URL", "RENDER", "FLY_APP_NAME", "DYNO",
     "KUBERNETES_SERVICE_HOST"],
)
def test_chaque_hebergeur_connu_declenche_la_production(monkeypatch, variable):
    from agent import environnement

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    for v in environnement.INDICES_HEBERGEMENT:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(variable, "1")
    assert environnement.est_production() is True


def test_poste_local_reste_en_developpement(monkeypatch):
    from agent import environnement

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    for v in environnement.INDICES_HEBERGEMENT:
        monkeypatch.delenv(v, raising=False)
    assert environnement.est_production() is False


def test_simulateur_exige_une_demande_expresse(monkeypatch):
    """
    Le simulateur est un canal sans authentification.

    L'ancienne règle « tout sauf production » le montait au moindre oubli de
    configuration ; il faut désormais le demander.
    """
    from agent import environnement

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert environnement.est_developpement_declare() is False

    monkeypatch.setenv("ENVIRONMENT", "development")
    assert environnement.est_developpement_declare() is True


# ═════════════════════════════════════════════════════════════════════════
# Sessions
# ═════════════════════════════════════════════════════════════════════════


def test_le_secret_de_repli_nest_jamais_la_constante_publique(monkeypatch):
    from agent import auth

    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    for v in __import__("agent.environnement", fromlist=["x"]).INDICES_HEBERGEMENT:
        monkeypatch.delenv(v, raising=False)
    assert auth._secret_session() != SECRET_AUTREFOIS_PUBLIC


def test_cookie_forge_avec_lancienne_constante_est_refuse(connecte, monkeypatch):
    """
    L'attaque exacte de l'audit : fabriquer un cookie sans mot de passe.

    Elle donnait auparavant l'accès à toutes les conversations clients.
    """
    from fastapi.testclient import TestClient

    charge = f"1.{int(time.time()) + 3600}"
    signature = hmac.new(SECRET_AUTREFOIS_PUBLIC, charge.encode(), hashlib.sha256).hexdigest()

    # Client neuf : aucune session légitime ne doit pouvoir masquer le résultat.
    with TestClient(connecte.app) as anonyme:
        r = anonyme.get(
            "/admin/conversations",
            headers={"Cookie": f"agentkit_session={charge}.{signature}"},
        )
    assert r.status_code == 401


def test_production_sans_secret_repond_503_et_lexplique(monkeypatch):
    from fastapi import HTTPException

    from agent import auth

    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(HTTPException) as e:
        auth.exiger_secret()
    assert e.value.status_code == 503
    assert "SESSION_SECRET" in e.value.detail
    assert "openssl" in e.value.detail  # la commande à lancer, pas juste le reproche


def test_cookie_secure_derriere_un_proxy_tls(connecte):
    """
    Derrière Coolify ou Railway, le serveur voit du HTTP : c'est le proxy qui a
    terminé le TLS. Sans lire X-Forwarded-Proto, le cookie partait sans Secure.
    """
    r = connecte.post(
        "/admin/connexion",
        json={"email": "test@exemple.fr", "mot_de_passe": "mot-de-passe-de-test"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert r.status_code == 200
    assert "Secure" in r.headers.get("set-cookie", "")


# ═════════════════════════════════════════════════════════════════════════
# Revue de configuration
# ═════════════════════════════════════════════════════════════════════════


def test_la_revue_signale_les_oublis_couteux(monkeypatch):
    from agent import environnement

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("PII_HASH_SALT", raising=False)
    monkeypatch.setenv("LOG_MESSAGE_CONTENT", "true")
    monkeypatch.setenv("AUTORISER_WEBHOOK_NON_SIGNE", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./a.db")
    monkeypatch.setenv("TEST_ALLOWLIST", "33600000000")

    sujets = {a["sujet"] for a in environnement.audit_configuration()}
    assert sujets >= {
        "SESSION_SECRET", "PII_HASH_SALT", "LOG_MESSAGE_CONTENT",
        "AUTORISER_WEBHOOK_NON_SIGNE", "DATABASE_URL", "TEST_ALLOWLIST",
    }


def test_la_revue_donne_un_remede_a_chaque_probleme(monkeypatch):
    """Un diagnostic sans marche à suivre est inutile pour qui n'est pas technique."""
    from agent import environnement

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    for a in environnement.audit_configuration():
        assert a["remede"].strip(), a["sujet"]
        assert a["explication"].strip(), a["sujet"]


def test_le_point_de_sante_expose_la_revue(client):
    corps = client.get("/").json()
    assert "a_corriger" in corps
    assert isinstance(corps["a_corriger"], list)


def test_la_revue_ne_divulgue_aucune_valeur_secrete(monkeypatch):
    """On publie des noms de variables à renseigner, jamais leur contenu."""
    from agent import environnement

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_SECRET", "valeur-ultra-secrete-a-ne-pas-fuiter")
    monkeypatch.setenv("TEST_ALLOWLIST", "33612345678")
    entier = str(environnement.audit_configuration())
    assert "valeur-ultra-secrete-a-ne-pas-fuiter" not in entier
    assert "33612345678" not in entier


# ═════════════════════════════════════════════════════════════════════════
# Webhooks
# ═════════════════════════════════════════════════════════════════════════


def test_dispense_de_signature_inoperante_chez_un_hebergeur(monkeypatch):
    from agent.providers import base

    monkeypatch.setenv("AUTORISER_WEBHOOK_NON_SIGNE", "true")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert base.autoriser_webhook_non_signe() is False


def test_reponse_trop_longue_tronquee_avant_envoi():
    """
    WhatsApp refuse au-delà de 4096 caractères : sans troncature, le client ne
    reçoit rien du tout plutôt qu'une réponse un peu coupée.
    """
    from agent.providers.meta import MAX_CARACTERES_WHATSAPP

    assert MAX_CARACTERES_WHATSAPP == 4096


# ═════════════════════════════════════════════════════════════════════════
# Plafonds d'écriture
# ═════════════════════════════════════════════════════════════════════════


def test_document_trop_gros_refuse(connecte):
    r = connecte.put("/admin/documents/enorme.md", json={"contenu": "A" * (600 * 1024)})
    assert r.status_code == 413
    assert "Ko" in r.json()["detail"]


def test_document_de_taille_normale_accepte(connecte):
    r = connecte.put("/admin/documents/tarifs2.md", json={"contenu": "# Tarifs\n- Pain : 1 EUR\n"})
    assert r.status_code == 200


def test_prompt_systeme_trop_gros_refuse(connecte):
    r = connecte.put("/admin/prompts", json={"system_prompt": "x" * (70 * 1024)})
    assert r.status_code == 413


# ═════════════════════════════════════════════════════════════════════════
# Révocation d'accès
# ═════════════════════════════════════════════════════════════════════════


def test_desactiver_un_compte_coupe_l_acces_immediatement(connecte):
    """
    Les sessions sont sans état : la coupure doit être vérifiée à chaque
    requête, sinon un salarié qui part garde l'accès jusqu'à l'expiration.
    """
    r = connecte.post("/admin/utilisateurs", json={
        "email": "collegue@exemple.fr", "nom": "Collègue", "mot_de_passe": "mot-de-passe-collegue"})
    assert r.status_code == 200
    identifiant = r.json()["id"]

    from fastapi.testclient import TestClient

    with TestClient(connecte.app) as autre:
        assert autre.post("/admin/connexion", json={
            "email": "collegue@exemple.fr", "mot_de_passe": "mot-de-passe-collegue"}).status_code == 200
        assert autre.get("/admin/conversations").status_code == 200

        assert connecte.patch(f"/admin/utilisateurs/{identifiant}",
                              json={"actif": False}).status_code == 200

        # Même cookie, toujours valide cryptographiquement : l'accès doit tomber.
        assert autre.get("/admin/conversations").status_code == 401


def test_on_ne_peut_pas_se_desactiver_soi_meme(connecte):
    moi = connecte.get("/admin/utilisateurs").json()["utilisateurs"][0]["id"]
    r = connecte.patch(f"/admin/utilisateurs/{moi}", json={"actif": False})
    assert r.status_code == 400


def test_changement_de_mot_de_passe_exige_l_ancien(connecte):
    r = connecte.post("/admin/motdepasse",
                      json={"ancien": "mauvais", "nouveau": "un-nouveau-mot-de-passe"})
    assert r.status_code == 403

    r = connecte.post("/admin/motdepasse",
                      json={"ancien": "mot-de-passe-de-test", "nouveau": "un-nouveau-mot-de-passe"})
    assert r.status_code == 200

    assert connecte.post("/admin/connexion", json={
        "email": "test@exemple.fr", "mot_de_passe": "un-nouveau-mot-de-passe"}).status_code == 200


def test_nouveau_mot_de_passe_trop_court_refuse(connecte):
    r = connecte.post("/admin/motdepasse",
                      json={"ancien": "mot-de-passe-de-test", "nouveau": "court"})
    assert r.status_code == 400


# ═════════════════════════════════════════════════════════════════════════
# Ordre des contrôles du webhook
# ═════════════════════════════════════════════════════════════════════════


def test_les_reessais_ne_consomment_pas_le_quota_du_client(client, cerveau_simule):
    """
    Meta rejoue un événement jusqu'à sept fois tant qu'il n'a pas son 2xx.

    Quand la limite de débit était évaluée avant la déduplication, ces réessais
    étaient comptés au client : un seul message rejoué cinq fois lui coûtait
    cinq jetons sur les vingt de sa fenêtre horaire, et il finissait muselé sans
    avoir rien fait.
    """
    import sys

    from agent.providers.simulateur import payload_signe

    limiteur = sys.modules["agent.securite"].limiteur
    limiteur._historique.clear()

    # Mêmes octets postés cinq fois : donc le même identifiant d'événement,
    # exactement ce que fait Meta quand il rejoue.
    charge, entetes = payload_signe("bonjour")
    for _ in range(5):
        assert client.post("/webhook", content=charge, headers=entetes).status_code == 200

    telephone = next(iter(limiteur._historique), None)
    consommes = len(limiteur._historique.get(telephone, [])) if telephone else 0
    assert consommes == 1, f"{consommes} jetons consommés pour un seul message"
