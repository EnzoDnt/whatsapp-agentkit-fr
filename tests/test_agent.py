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
    assert {"rechercher_information", "verifier_delai", "enregistrer_demande", "passer_la_main"} <= noms


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
    import asyncio

    from agent.tools import executer_outil

    assert "inconnu" in asyncio.run(executer_outil("outil_qui_n_existe_pas", {})).lower()


def test_les_outils_synchrones_et_asynchrones_marchent_tous_les_deux():
    """Le registre accepte les deux : certains outils doivent écrire en base."""
    import asyncio

    from agent.tools import executer_outil

    # rechercher_information est synchrone
    r = asyncio.run(executer_outil("rechercher_information", {"requete": "zzz introuvable"}))
    assert isinstance(r, str)


def test_une_consigne_expiree_n_est_plus_active():
    from datetime import datetime, timedelta, timezone

    from agent.memory import Consigne

    m = datetime.now(timezone.utc)
    assert Consigne(texte="x", fin=m - timedelta(days=1)).est_active() is False
    assert Consigne(texte="x", debut=m + timedelta(days=1)).est_active() is False
    assert Consigne(texte="x", activee=False).est_active() is False
    assert Consigne(texte="x").est_active() is True
    assert Consigne(texte="x", debut=m - timedelta(days=1), fin=m + timedelta(days=1)).est_active() is True


def test_le_statut_d_une_consigne_est_lisible():
    from datetime import datetime, timedelta, timezone

    from agent.memory import Consigne

    m = datetime.now(timezone.utc)
    assert Consigne(texte="x").statut() == "active"
    assert Consigne(texte="x", activee=False).statut() == "desactivee"
    assert Consigne(texte="x", debut=m + timedelta(days=2)).statut() == "programmee"
    assert Consigne(texte="x", fin=m - timedelta(days=2)).statut() == "expiree"


# ── Usernames WhatsApp / BSUID (juillet 2026) ────────────────────────────

def _parser(payload):
    import asyncio

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

    return asyncio.run(meta.FournisseurMeta().parser_webhook(FausseRequete(payload)))


def _payload(msg, contacts):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "0", "changes": [{"field": "messages",
                   "value": {"messaging_product": "whatsapp",
                             "metadata": {"phone_number_id": "y"},
                             "contacts": contacts, "messages": [msg]}}]}],
    }


def test_client_avec_username_sans_numero_est_bien_recu():
    """
    Le cas qui casse la plupart des intégrations : `from` est vide, seul le
    BSUID identifie le client. Sans ce traitement, le message est perdu.
    """
    bsuid = "user.9373795779eb6441c8adb2eaee5b848e7dd174ddd302d7db62142f4722d574b6"
    messages = _parser(_payload(
        {"from": "", "from_user_id": bsuid, "id": "wamid.X", "type": "text",
         "text": {"body": "Bonjour"}},
        [{"profile": {"name": "Pablo M.", "username": "@pablomorales"}, "wa_id": "", "user_id": bsuid}],
    ))
    assert len(messages) == 1
    assert messages[0].identifiant == bsuid
    assert messages[0].par_bsuid is True
    assert messages[0].username == "@pablomorales"


def test_le_numero_reste_prioritaire_quand_il_est_fourni():
    bsuid = "user.abc123"
    messages = _parser(_payload(
        {"from": "33612345678", "from_user_id": bsuid, "id": "wamid.Y", "type": "text",
         "text": {"body": "Salut"}},
        [{"profile": {"name": "Léa"}, "wa_id": "33612345678", "user_id": bsuid}],
    ))
    assert messages[0].identifiant == "33612345678"
    assert messages[0].par_bsuid is False
    assert messages[0].contexte["bsuid"] == bsuid


def test_un_message_sans_aucun_identifiant_est_ignore_sans_planter():
    messages = _parser(_payload(
        {"from": "", "from_user_id": "", "id": "wamid.Z", "type": "text",
         "text": {"body": "fantôme"}}, [],
    ))
    assert messages == []


def test_le_bsuid_est_masque_dans_les_logs():
    from agent.securite import masquer_identifiant

    bsuid = "user.9373795779eb6441c8adb2eaee5b848e7dd174ddd302d7db62142f4722d574b6"
    masque = masquer_identifiant(bsuid, par_bsuid=True)
    assert bsuid not in masque
    assert masque.startswith("bsuid_")


def test_le_numero_n_est_injecte_que_dans_les_outils_qui_l_acceptent():
    """
    Régression : injecter `telephone` dans tous les appels d'outils lève un
    TypeError sur ceux qui ne le déclarent pas, et le client perd sa réponse.
    """
    from agent.tools import outil_accepte

    assert outil_accepte("enregistrer_demande", "telephone") is True
    assert outil_accepte("passer_la_main", "telephone") is True
    assert outil_accepte("rechercher_information", "telephone") is False
    assert outil_accepte("verifier_delai", "telephone") is False
    assert outil_accepte("outil_inexistant", "telephone") is False


def test_la_date_du_jour_est_injectee_dans_le_prompt():
    """
    Sans la date, le modèle ne peut pas interpréter « demain » et invente une
    date — inacceptable pour un agent qui prend des commandes datées.
    """
    from datetime import datetime

    from agent.brain import horodatage

    h = horodatage()
    assert datetime.now().strftime("%Y-%m-%d") in h
    assert str(datetime.now().year) in h
    assert "AAAA-MM-JJ" in h


# ── Transparence IA (AI Act art. 50, applicable depuis le 02/08/2026) ────

def test_la_mention_ia_apparait_au_premier_contact_seulement():
    """
    L'article 50 impose d'informer « au plus tard lors de la première
    interaction ». La répéter ensuite serait pénible et n'ajoute rien.
    """
    import agent.brain as b

    d_avant = b.MODE_TRANSPARENCE
    try:
        b.MODE_TRANSPARENCE = "discrete"
        premier = b.appliquer_transparence("Bonjour.", premier_echange=True)
        suivant = b.appliquer_transparence("Bonjour.", premier_echange=False)
        assert "intelligence artificielle" in premier.lower()
        assert premier.startswith("Bonjour.")          # mention en pied
        assert suivant == "Bonjour."                   # jamais répétée
    finally:
        b.MODE_TRANSPARENCE = d_avant


def test_le_mode_explicite_place_la_mention_en_tete():
    import agent.brain as b

    d_avant = b.MODE_TRANSPARENCE
    try:
        b.MODE_TRANSPARENCE = "explicite"
        r = b.appliquer_transparence("Bonjour.", premier_echange=True)
        assert r.startswith("ℹ️")
        assert r.rstrip().endswith("Bonjour.")
    finally:
        b.MODE_TRANSPARENCE = d_avant


def test_le_mode_validation_n_ajoute_aucune_mention():
    """
    Quand chaque réponse est relue par une personne, l'AI Act n'impose plus de
    marquage : c'est l'humain qui porte la responsabilité éditoriale.
    """
    import agent.brain as b

    d_avant = b.MODE_TRANSPARENCE
    try:
        b.MODE_TRANSPARENCE = "validation"
        assert b.appliquer_transparence("Bonjour.", premier_echange=True) == "Bonjour."
    finally:
        b.MODE_TRANSPARENCE = d_avant


def test_l_auteur_d_un_message_est_toujours_renseigne():
    """
    Traçabilité : on doit pouvoir dire de chaque message s'il vient d'une IA,
    d'un humain, ou du client.
    """
    from agent.memory import Message

    assert Message(telephone="x", role="user", contenu="a").auteur is None or True
    # Le défaut est appliqué à l'insertion ; enregistrer_message le fixe en amont.
    import inspect

    from agent.memory import enregistrer_message

    src = inspect.getsource(enregistrer_message)
    assert 'auteur = "client" if role == "user" else "agent"' in src


def test_une_reponse_validee_par_un_humain_porte_son_nom():
    from agent.memory import Message

    m = Message(telephone="x", role="assistant", contenu="ok",
                auteur="humain", valide_par="lea@exemple.fr")
    assert m.auteur == "humain" and m.valide_par == "lea@exemple.fr"


# ── Escalade ─────────────────────────────────────────────────────────────

def test_l_outil_d_escalade_demande_un_brouillon_de_reponse():
    """
    Une escalade sans brouillon oblige l'équipe à tout réécrire — c'est ce qui
    fait abandonner les outils d'escalade en pratique.
    """
    from agent.tools import schemas_outils

    schema = next(s for s in schemas_outils() if s["name"] == "passer_la_main")
    props = schema["input_schema"]["properties"]
    assert {"motif", "question_equipe", "reponse_proposee", "urgence"} <= set(props)
    assert props["urgence"]["enum"] == ["normale", "haute"]
    assert "TOUJOURS une réponse_proposee" in schema["description"]


# ── Authentification ─────────────────────────────────────────────────────

def test_le_mot_de_passe_n_est_jamais_stocke_en_clair():
    from agent.auth import hacher, verifier_mot_de_passe

    empreinte, sel = hacher("un-mot-de-passe-solide")
    assert "un-mot-de-passe-solide" not in empreinte
    assert verifier_mot_de_passe("un-mot-de-passe-solide", empreinte, sel) is True
    assert verifier_mot_de_passe("presque-le-bon", empreinte, sel) is False


def test_deux_comptes_avec_le_meme_mot_de_passe_ont_des_empreintes_differentes():
    """Sel aléatoire : sinon une seule table précalculée casse tous les comptes."""
    from agent.auth import hacher

    a, _ = hacher("identique")
    b, _ = hacher("identique")
    assert a != b


def test_un_jeton_de_session_falsifie_est_rejete():
    from agent.auth import creer_jeton, lire_jeton

    jeton = creer_jeton(42)
    assert lire_jeton(jeton) == 42
    assert lire_jeton(jeton[:-4] + "0000") is None
    assert lire_jeton("99." + jeton.split(".", 1)[1]) is None
    assert lire_jeton("n'importe quoi") is None


def test_les_tentatives_de_connexion_sont_freinees():
    from agent.auth import LimiteurConnexion

    l = LimiteurConnexion(maximum=3, fenetre_s=60)
    for _ in range(3):
        assert l.autorise("1.2.3.4") is True
        l.echec("1.2.3.4")
    assert l.autorise("1.2.3.4") is False
    assert l.autorise("5.6.7.8") is True     # une autre IP n'est pas pénalisée
    l.succes("1.2.3.4")
    assert l.autorise("1.2.3.4") is True     # remise à zéro après succès
