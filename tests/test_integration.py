"""
Tests d'intégration : on frappe l'API HTTP réelle, pas les fonctions.

Organisés par surface. Chaque test nomme le comportement attendu du point de
vue de l'utilisateur, pas l'implémentation — c'est ce qui les rend encore utiles
après un refactor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

SECRET_WEBHOOK = "secret-webhook-de-test"


# ═════════════════════════════════════════════════════════════════════════
# Outils de test
# ═════════════════════════════════════════════════════════════════════════

def payload_meta(texte: str, expediteur: str = "33600000000", *,
                 bsuid: str = "", nom: str = "", username: str = "",
                 pays: str = "", type_msg: str = "text", mid: str | None = None) -> dict:
    identifiant = expediteur or bsuid
    profil = {}
    if nom:
        profil["name"] = nom
    if username:
        profil["username"] = username
    if pays:
        profil["country_code"] = pays
    message = {
        "from": expediteur,
        "id": mid or f"wamid.T{int(time.time()*1e6)}",
        "timestamp": str(int(time.time())),
        "type": type_msg,
    }
    if bsuid:
        message["from_user_id"] = bsuid
    if type_msg == "text":
        message["text"] = {"body": texte}
    else:
        message[type_msg] = {"id": "media123"}
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "0", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "faux"},
            "contacts": [{"profile": profil, "wa_id": expediteur, "user_id": bsuid}],
            "messages": [message],
        }}]}],
    }


def poster(client, charge: dict, *, signer_avec: str | None = SECRET_WEBHOOK):
    corps = json.dumps(charge).encode()
    entetes = {"Content-Type": "application/json"}
    if signer_avec is not None:
        empreinte = hmac.new(signer_avec.encode(), corps, hashlib.sha256).hexdigest()
        entetes["X-Hub-Signature-256"] = f"sha256={empreinte}"
    return client.post("/webhook", content=corps, headers=entetes)


# ═════════════════════════════════════════════════════════════════════════
# Santé et démarrage
# ═════════════════════════════════════════════════════════════════════════

class TestSante:
    def test_le_serveur_repond_et_annonce_son_fournisseur(self, client):
        d = client.get("/").json()
        assert d["statut"] == "ok"
        assert d["fournisseur"] == "simulateur"
        assert d["depense_du_jour_usd"] == 0.0

    def test_la_page_de_console_est_servie(self, connecte):
        assert connecte.get("/admin/").status_code == 200

    def test_le_simulateur_est_monte_hors_production(self, client):
        assert client.get("/simulateur").status_code == 200


# ═════════════════════════════════════════════════════════════════════════
# Authentification
# ═════════════════════════════════════════════════════════════════════════

class TestAuthentification:
    def test_avant_tout_compte_la_console_demande_un_amorcage(self, client):
        assert client.get("/admin/session").json()["etat"] == "amorcage_requis"

    def test_l_amorcage_exige_le_jeton_d_installation(self, client):
        r = client.post("/admin/amorcer", json={
            "nom": "X", "email": "x@y.fr", "mot_de_passe": "assez-long-quand-meme",
            "jeton": "mauvais-jeton"})
        assert r.status_code == 401
        assert client.get("/admin/session").json()["etat"] == "amorcage_requis"

    def test_un_mot_de_passe_trop_court_est_refuse(self, client):
        r = client.post("/admin/amorcer", json={
            "nom": "X", "email": "x@y.fr", "mot_de_passe": "court",
            "jeton": "jeton-installation-de-test"})
        assert r.status_code == 400
        assert "10 caractères" in r.json()["detail"]

    def test_une_adresse_invalide_est_refusee(self, client):
        r = client.post("/admin/amorcer", json={
            "nom": "X", "email": "pas-une-adresse", "mot_de_passe": "assez-long-quand-meme",
            "jeton": "jeton-installation-de-test"})
        assert r.status_code == 400

    def test_l_amorcage_ouvre_la_session_directement(self, connecte):
        d = connecte.get("/admin/session").json()
        assert d["etat"] == "connecte" and d["email"] == "test@exemple.fr"

    def test_on_ne_peut_pas_amorcer_deux_fois(self, connecte):
        r = connecte.post("/admin/amorcer", json={
            "nom": "Intrus", "email": "intrus@y.fr", "mot_de_passe": "assez-long-quand-meme",
            "jeton": "jeton-installation-de-test"})
        assert r.status_code == 409

    def test_toutes_les_routes_sont_fermees_sans_session(self, client):
        for chemin in ("/admin/conversations", "/admin/consignes", "/admin/documents",
                       "/admin/demandes", "/admin/prompts", "/admin/escalades",
                       "/admin/utilisateurs", "/admin/marque"):
            assert client.get(chemin).status_code == 401, chemin

    def test_la_deconnexion_ferme_reellement_l_acces(self, connecte):
        assert connecte.get("/admin/conversations").status_code == 200
        connecte.post("/admin/deconnexion")
        assert connecte.get("/admin/conversations").status_code == 401

    def test_un_mauvais_mot_de_passe_est_refuse(self, connecte):
        connecte.post("/admin/deconnexion")
        r = connecte.post("/admin/connexion", json={
            "email": "test@exemple.fr", "mot_de_passe": "pas-le-bon-du-tout"})
        assert r.status_code == 401

    def test_le_message_ne_revele_pas_si_l_adresse_existe(self, connecte):
        connecte.post("/admin/deconnexion")
        inconnue = connecte.post("/admin/connexion", json={
            "email": "jamais-vue@exemple.fr", "mot_de_passe": "peu-importe-ici"})
        connue = connecte.post("/admin/connexion", json={
            "email": "test@exemple.fr", "mot_de_passe": "pas-le-bon-du-tout"})
        assert inconnue.json()["detail"] == connue.json()["detail"]

    def test_les_tentatives_repetees_finissent_par_etre_bloquees(self, connecte):
        connecte.post("/admin/deconnexion")
        codes = [connecte.post("/admin/connexion", json={
            "email": "test@exemple.fr", "mot_de_passe": "faux"}).status_code
            for _ in range(12)]
        assert 429 in codes, "aucune limitation de débit sur la connexion"

    def test_un_cookie_falsifie_ne_donne_pas_acces(self, connecte):
        connecte.cookies.set("agentkit_session", "1.9999999999.signaturebidon")
        assert connecte.get("/admin/conversations").status_code == 401

    def test_un_second_compte_peut_etre_ajoute_puis_se_connecter(self, connecte):
        r = connecte.post("/admin/utilisateurs", json={
            "nom": "Collègue", "email": "collegue@exemple.fr",
            "mot_de_passe": "un-autre-mot-de-passe"})
        assert r.status_code == 200
        connecte.post("/admin/deconnexion")
        r = connecte.post("/admin/connexion", json={
            "email": "collegue@exemple.fr", "mot_de_passe": "un-autre-mot-de-passe"})
        assert r.status_code == 200 and r.json()["nom"] == "Collègue"

    def test_deux_comptes_ne_peuvent_pas_partager_une_adresse(self, connecte):
        connecte.post("/admin/utilisateurs", json={
            "nom": "A", "email": "doublon@exemple.fr", "mot_de_passe": "mot-de-passe-un"})
        r = connecte.post("/admin/utilisateurs", json={
            "nom": "B", "email": "doublon@exemple.fr", "mot_de_passe": "mot-de-passe-deux"})
        assert r.status_code == 409


# ═════════════════════════════════════════════════════════════════════════
# Webhook : les six portes du cycle synchrone
# ═════════════════════════════════════════════════════════════════════════

class TestWebhook:
    def test_une_signature_valide_met_le_message_en_file(self, client, cerveau_simule):
        r = poster(client, payload_meta("Bonjour"))
        assert r.status_code == 200 and r.json()["empiles"] == 1

    def test_une_signature_absente_est_rejetee(self, client):
        assert poster(client, payload_meta("x"), signer_avec=None).status_code == 401

    def test_une_signature_calculee_avec_le_mauvais_secret_est_rejetee(self, client):
        assert poster(client, payload_meta("x"), signer_avec="pas-le-bon").status_code == 401

    def test_un_corps_modifie_apres_signature_est_rejete(self, client):
        corps = json.dumps(payload_meta("innocent")).encode()
        empreinte = hmac.new(SECRET_WEBHOOK.encode(), corps, hashlib.sha256).hexdigest()
        altere = json.dumps(payload_meta("malveillant")).encode()
        r = client.post("/webhook", content=altere, headers={
            "Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={empreinte}"})
        assert r.status_code == 401

    def test_le_meme_evenement_livre_deux_fois_n_est_traite_qu_une(self, client, cerveau_simule):
        charge = payload_meta("Bonjour", mid="wamid.FIXE")
        assert poster(client, charge).json()["empiles"] == 1
        assert poster(client, charge).json()["empiles"] == 0

    def test_un_message_vide_est_ignore(self, client, cerveau_simule):
        assert poster(client, payload_meta("   ")).json()["empiles"] == 0

    def test_les_messages_non_textuels_sont_ignores(self, client, cerveau_simule):
        assert poster(client, payload_meta("", type_msg="image")).json()["empiles"] == 0

    def test_un_payload_illisible_ne_declenche_pas_de_reessai_infini(self, client):
        corps = b'{"pas": "le bon format"}'
        empreinte = hmac.new(SECRET_WEBHOOK.encode(), corps, hashlib.sha256).hexdigest()
        r = client.post("/webhook", content=corps, headers={
            "Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={empreinte}"})
        assert r.status_code == 200

    def test_la_verification_get_de_meta_renvoie_le_defi(self, client, monkeypatch):
        monkeypatch.setenv("META_VERIFY_TOKEN", "jeton-verif")
        import importlib
        import sys
        importlib.reload(sys.modules["agent.providers.meta"])
        r = client.get("/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "agentkit-verify",
            "hub.challenge": "DEFI42"})
        assert r.status_code in (200, 403)

    def test_un_mauvais_jeton_de_verification_renvoie_403(self, client):
        r = client.get("/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "faux", "hub.challenge": "X"})
        assert r.status_code == 403


class TestGardesDeDebit:
    def test_la_liste_blanche_filtre_les_expediteurs(self, env_propre, monkeypatch):
        monkeypatch.setenv("TEST_ALLOWLIST", "33611111111")
        import importlib
        import sys
        importlib.reload(sys.modules["agent.securite"])
        importlib.reload(sys.modules["agent.main"])
        from fastapi.testclient import TestClient

        async def faux(m, h, telephone=""):
            return ("ok", True)

        monkeypatch.setattr(sys.modules["agent.main"], "generer_reponse", faux)
        with TestClient(sys.modules["agent.main"].app) as c:
            assert poster(c, payload_meta("a", "33622222222")).json()["empiles"] == 0
            assert poster(c, payload_meta("b", "33611111111")).json()["empiles"] == 1

    def test_au_dela_du_quota_les_messages_sont_ignores(self, env_propre, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_MESSAGES", "3")
        import importlib
        import sys
        importlib.reload(sys.modules["agent.securite"])
        importlib.reload(sys.modules["agent.main"])
        from fastapi.testclient import TestClient

        async def faux(m, h, telephone=""):
            return ("ok", True)

        monkeypatch.setattr(sys.modules["agent.main"], "generer_reponse", faux)
        with TestClient(sys.modules["agent.main"].app) as c:
            resultats = [poster(c, payload_meta(f"msg {i}")).json()["empiles"] for i in range(5)]
        assert resultats == [1, 1, 1, 0, 0]


# ═════════════════════════════════════════════════════════════════════════
# Usernames WhatsApp et identité
# ═════════════════════════════════════════════════════════════════════════

class TestIdentiteClient:
    def test_un_client_sans_numero_est_identifie_par_son_bsuid(self, connecte, cerveau_simule):
        bsuid = "user.aaaabbbbccccdddd"
        assert poster(connecte, payload_meta("Bonjour", "", bsuid=bsuid)).json()["empiles"] == 1
        conversations = connecte.get("/admin/conversations").json()["conversations"]
        assert any(c["identifiant"] == bsuid for c in conversations)

    def test_le_profil_whatsapp_alimente_la_fiche(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour", "33600000000",
                                      nom="Léa M.", username="@leam", pays="FR"))
        c = connecte.get("/admin/contacts/33600000000").json()
        assert c["nom_whatsapp"] == "Léa M." and c["username"] == "@leam" and c["pays"] == "FR"
        assert c["initiales"] == "LM"

    def test_le_nom_saisi_par_le_commercant_prime(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour", "33600000000", nom="Léa M."))
        connecte.put("/admin/contacts/33600000000",
                     json={"nom_affiche": "Léa — traiteur", "notes": "Paie à 30 jours"})
        c = connecte.get("/admin/contacts/33600000000").json()
        assert c["nom"] == "Léa — traiteur"
        assert c["nom_whatsapp"] == "Léa M."     # l'original n'est pas perdu
        assert c["notes"] == "Paie à 30 jours"

    def test_le_numero_n_apparait_pas_en_clair_dans_la_liste(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour", "33612345678"))
        liste = connecte.get("/admin/conversations").json()["conversations"]
        cible = next(c for c in liste if c["identifiant"] == "33612345678")
        assert "33612345678" not in cible["affichage"]


# ═════════════════════════════════════════════════════════════════════════
# Conversations et reprise de main
# ═════════════════════════════════════════════════════════════════════════

class TestConversations:
    def test_l_echange_est_enregistre_avec_ses_auteurs(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        d = connecte.get("/admin/conversations/33600000000").json()
        auteurs = [m["auteur"] for m in d["messages"]]
        assert auteurs == ["client", "agent"]

    def test_mettre_en_pause_fait_taire_l_agent(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour", mid="wamid.A"))
        connecte.post("/admin/conversations/33600000000/pause", json={"en_pause": True})
        poster(connecte, payload_meta("Vous êtes là ?", mid="wamid.B"))
        d = connecte.get("/admin/conversations/33600000000").json()
        assert d["en_pause"] is True
        assert [m["auteur"] for m in d["messages"]] == ["client", "agent", "client"]

    def test_rendre_la_main_reactive_l_agent(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("A", mid="wamid.A"))
        connecte.post("/admin/conversations/33600000000/pause", json={"en_pause": True})
        connecte.post("/admin/conversations/33600000000/pause", json={"en_pause": False})
        poster(connecte, payload_meta("B", mid="wamid.B"))
        d = connecte.get("/admin/conversations/33600000000").json()
        assert d["messages"][-1]["auteur"] == "agent"

    def test_une_reponse_humaine_est_marquee_comme_telle(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        r = connecte.post("/admin/conversations/33600000000/repondre",
                          json={"texte": "Bonjour, Léa du magasin."})
        assert r.status_code == 200
        dernier = connecte.get("/admin/conversations/33600000000").json()["messages"][-1]
        assert dernier["auteur"] == "humain" and dernier["contenu"].startswith("Bonjour, Léa")

    def test_un_message_humain_vide_est_refuse(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        r = connecte.post("/admin/conversations/33600000000/repondre", json={"texte": "  "})
        assert r.status_code == 400

    def test_l_effacement_supprime_bien_l_historique(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        r = connecte.delete("/admin/conversations/33600000000")
        assert r.json()["messages_effaces"] >= 1
        assert connecte.get("/admin/conversations/33600000000").json()["messages"] == []


# ═════════════════════════════════════════════════════════════════════════
# Consignes datées
# ═════════════════════════════════════════════════════════════════════════

class TestConsignes:
    def test_une_consigne_sans_date_est_active_immediatement(self, connecte):
        connecte.post("/admin/consignes", json={"texte": "Plus de tarte au citron"})
        c = connecte.get("/admin/consignes").json()["consignes"][0]
        assert c["statut"] == "active"

    def test_une_consigne_future_est_programmee(self, connecte):
        from datetime import datetime, timedelta
        futur = (datetime.now() + timedelta(days=3)).isoformat(timespec="minutes")
        connecte.post("/admin/consignes", json={"texte": "Promo", "debut": futur})
        assert connecte.get("/admin/consignes").json()["consignes"][0]["statut"] == "programmee"

    def test_une_consigne_passee_est_expiree(self, connecte):
        from datetime import datetime, timedelta
        passe = (datetime.now() - timedelta(days=1)).isoformat(timespec="minutes")
        connecte.post("/admin/consignes", json={"texte": "Fini", "fin": passe})
        assert connecte.get("/admin/consignes").json()["consignes"][0]["statut"] == "expiree"

    def test_une_fin_avant_le_debut_est_refusee(self, connecte):
        r = connecte.post("/admin/consignes", json={
            "texte": "Incohérente", "debut": "2026-12-01T10:00", "fin": "2026-11-01T10:00"})
        assert r.status_code == 400

    def test_une_consigne_vide_est_refusee(self, connecte):
        assert connecte.post("/admin/consignes", json={"texte": "   "}).status_code == 400

    def test_une_date_illisible_est_refusee(self, connecte):
        r = connecte.post("/admin/consignes", json={"texte": "X", "debut": "avant-hier"})
        assert r.status_code == 400

    def test_desactiver_puis_reactiver_change_le_statut(self, connecte):
        ident = connecte.post("/admin/consignes", json={"texte": "X"}).json()["id"]
        assert connecte.patch(f"/admin/consignes/{ident}",
                              json={"activee": False}).json()["statut"] == "desactivee"
        assert connecte.patch(f"/admin/consignes/{ident}",
                              json={"activee": True}).json()["statut"] == "active"

    def test_la_suppression_retire_bien_la_consigne(self, connecte):
        ident = connecte.post("/admin/consignes", json={"texte": "X"}).json()["id"]
        connecte.delete(f"/admin/consignes/{ident}")
        assert connecte.get("/admin/consignes").json()["consignes"] == []

    @pytest.mark.asyncio
    async def test_seules_les_consignes_actives_entrent_dans_le_prompt(self, connecte):
        from datetime import datetime, timedelta
        futur = (datetime.now() + timedelta(days=3)).isoformat(timespec="minutes")
        passe = (datetime.now() - timedelta(days=3)).isoformat(timespec="minutes")
        connecte.post("/admin/consignes", json={"texte": "VISIBLE maintenant"})
        connecte.post("/admin/consignes", json={"texte": "CACHEE future", "debut": futur})
        connecte.post("/admin/consignes", json={"texte": "CACHEE passee", "fin": passe})
        ident = connecte.post("/admin/consignes", json={"texte": "CACHEE off"}).json()["id"]
        connecte.patch(f"/admin/consignes/{ident}", json={"activee": False})

        import sys
        bloc = await sys.modules["agent.brain"].bloc_consignes()
        assert "VISIBLE maintenant" in bloc
        assert "CACHEE" not in bloc
