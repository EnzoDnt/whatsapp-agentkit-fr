"""
Cas durs : production, concurrence, pannes, épuisement.

Ces tests ne vérifient pas que ça marche quand tout va bien — les précédents
s'en chargent. Ils vérifient que ça se comporte correctement quand ça va mal.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys

import pytest

from tests.test_integration import payload_meta, poster


# ═════════════════════════════════════════════════════════════════════════
# Mode production
# ═════════════════════════════════════════════════════════════════════════

class TestProduction:
    def test_sans_secret_de_webhook_la_production_refuse_de_demarrer(self, env_propre, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("META_APP_SECRET", "")
        monkeypatch.setenv("AUTORISER_WEBHOOK_NON_SIGNE", "true")   # doit être ignoré
        monkeypatch.setenv("WHATSAPP_PROVIDER", "meta")
        for nom in ("agent.providers.base", "agent.providers.meta", "agent.providers"):
            importlib.reload(sys.modules[nom])
        base = sys.modules["agent.providers.base"]
        assert base.autoriser_webhook_non_signe() is False
        with pytest.raises(base.ErreurConfiguration):
            sys.modules["agent.providers.meta"].FournisseurMeta()

    def test_la_configuration_invalide_est_expliquee_dans_le_point_de_sante(
            self, env_propre, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("META_APP_SECRET", "")
        monkeypatch.setenv("WHATSAPP_PROVIDER", "meta")
        for nom in ("agent.providers.base", "agent.providers.meta",
                    "agent.providers", "agent.main"):
            importlib.reload(sys.modules[nom])
        from fastapi.testclient import TestClient
        # base_url https : en production le cookie de session est marqué Secure,
        # donc il n'est renvoyé que sur une connexion chiffrée — le cas réel
        # derrière le proxy TLS de Coolify.
        with TestClient(sys.modules["agent.main"].app, base_url="https://testserver") as c:
            # / reste public et muet, même en erreur de configuration.
            assert c.get("/").json() == {"statut": "actif"}
            # Le détail de l'erreur exige une session : on amorce un compte.
            r = c.post("/admin/amorcer", json={
                "nom": "T", "email": "t@e.fr", "mot_de_passe": "mot-de-passe-de-test",
                "jeton": "jeton-installation-de-test"})
            assert r.status_code == 200, r.text
            d = c.get("/admin/etat").json()
        assert d["statut"] == "erreur"
        assert "META_APP_SECRET" in d["detail"]

    def test_le_simulateur_n_est_pas_monte_en_production(self, env_propre, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        importlib.reload(sys.modules["agent.main"])
        from fastapi.testclient import TestClient
        with TestClient(sys.modules["agent.main"].app) as c:
            assert c.get("/simulateur").status_code == 404
            assert c.post("/simulateur/envoyer", json={"texte": "x"}).status_code == 404

    def test_sans_secret_de_session_la_production_refuse(self, env_propre, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET", "")
        # memory avant auth : auth déclare une table sur le Base de memory.
        importlib.reload(sys.modules["agent.memory"])
        importlib.reload(sys.modules["agent.auth"])
        with pytest.raises(RuntimeError, match="SESSION_SECRET"):
            sys.modules["agent.auth"]._secret_session()

    def test_en_local_le_contournement_reste_possible_mais_explicite(
            self, env_propre, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("META_APP_SECRET", "")
        monkeypatch.setenv("AUTORISER_WEBHOOK_NON_SIGNE", "true")
        importlib.reload(sys.modules["agent.providers.base"])
        assert sys.modules["agent.providers.base"].autoriser_webhook_non_signe() is True


# ═════════════════════════════════════════════════════════════════════════
# Concurrence
# ═════════════════════════════════════════════════════════════════════════

class TestConcurrence:
    def test_deux_livraisons_simultanees_du_meme_evenement_ne_passent_qu_une_fois(
            self, env_propre):
        """
        La déduplication repose sur la clé primaire, pas sur un SELECT préalable.
        Deux insertions concurrentes doivent donner un seul succès.
        """
        memoire = sys.modules["agent.memory"]

        async def scenario():
            await memoire.initialiser_base()
            resultats = await asyncio.gather(
                *[memoire.marquer_evenement_traite("evt-course") for _ in range(8)]
            )
            return resultats

        resultats = asyncio.run(scenario())
        assert sum(resultats) == 1, f"{sum(resultats)} passages au lieu d'un seul"

    def test_le_verrou_serialise_les_messages_d_un_meme_client(self, connecte, monkeypatch):
        """Deux messages rapprochés ne doivent pas lire le même historique."""
        ordre = []

        async def lent(message, historique, telephone=""):
            ordre.append(("entree", len(historique)))
            await asyncio.sleep(0.02)
            ordre.append(("sortie", len(historique)))
            return (f"réponse {len(historique)}", True)

        monkeypatch.setattr(sys.modules["agent.main"], "generer_reponse", lent)
        poster(connecte, payload_meta("un", mid="wamid.1"))
        poster(connecte, payload_meta("deux", mid="wamid.2"))
        # Chaque entrée doit être suivie de sa sortie : jamais deux entrées d'affilée.
        etapes = [e[0] for e in ordre]
        assert etapes == ["entree", "sortie", "entree", "sortie"], ordre

    def test_l_historique_du_second_message_contient_le_premier(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("un", mid="wamid.1"))
        poster(connecte, payload_meta("deux", mid="wamid.2"))
        messages = connecte.get("/admin/conversations/33600000000").json()["messages"]
        assert len(messages) == 4      # client, agent, client, agent

    def test_deux_clients_differents_ne_se_bloquent_pas(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("a", "33611111111"))
        poster(connecte, payload_meta("b", "33622222222"))
        identifiants = {c["identifiant"]
                        for c in connecte.get("/admin/conversations").json()["conversations"]}
        assert identifiants == {"33611111111", "33622222222"}


# ═════════════════════════════════════════════════════════════════════════
# Épuisement et pannes
# ═════════════════════════════════════════════════════════════════════════

class TestPannes:
    def test_le_plafond_de_depense_coupe_les_appels(self, connecte):
        cerveau = sys.modules["agent.brain"]
        securite = sys.modules["agent.securite"]
        securite.depenses.plafond_journalier = 0.001
        securite.depenses.enregistrer("claude-sonnet-5", 16000, 1200)
        try:
            texte, vraie = asyncio.run(cerveau.generer_reponse("Bonjour", []))
            assert vraie is False
            assert "limite" in texte.lower() or "quota" in texte.lower()
        finally:
            securite.depenses.plafond_journalier = 5.0
            securite.depenses._depense = 0.0

    def test_un_message_trop_court_ne_declenche_aucun_appel(self, connecte):
        cerveau = sys.modules["agent.brain"]
        texte, vraie = asyncio.run(cerveau.generer_reponse("a", []))
        assert vraie is False

    def test_une_panne_du_modele_ne_pollue_pas_l_historique(self, connecte, monkeypatch):
        """Un avis technique n'est pas un tour de conversation."""
        async def casse(message, historique, telephone=""):
            return ("Panne.", False)

        monkeypatch.setattr(sys.modules["agent.main"], "generer_reponse", casse)
        poster(connecte, payload_meta("Bonjour"))
        messages = connecte.get("/admin/conversations/33600000000").json()["messages"]
        # Le message du client est conservé, l'avis technique non.
        assert [m["auteur"] for m in messages] == ["client"]

    def test_un_echec_d_envoi_libere_l_evenement_pour_le_reessai(self, connecte, monkeypatch):
        from agent.providers.simulateur import FournisseurSimulateur

        echec = {"actif": True}

        async def envoi_capricieux(self, destinataire, message, contexte=None):
            return not echec["actif"]

        monkeypatch.setattr(FournisseurSimulateur, "envoyer_message", envoi_capricieux)

        async def ok(m, h, telephone=""):
            return ("réponse", True)

        monkeypatch.setattr(sys.modules["agent.main"], "generer_reponse", ok)

        charge = payload_meta("Bonjour", mid="wamid.REESSAI")
        assert poster(connecte, charge).json()["empiles"] == 1   # échoue à l'envoi
        echec["actif"] = False
        # Le réessai de Meta doit repasser : sinon le client n'a jamais de réponse.
        assert poster(connecte, charge).json()["empiles"] == 1

    def test_un_outil_qui_leve_une_exception_est_rattrape(self, connecte, monkeypatch):
        outils = sys.modules["agent.tools"]

        def explose(requete: str) -> str:
            raise RuntimeError("disque plein")

        monkeypatch.setitem(outils._REGISTRE, "rechercher_information",
                            (outils._REGISTRE["rechercher_information"][0], explose))
        r = asyncio.run(outils.executer_outil("rechercher_information", {"requete": "x"}))
        assert "disque plein" in r        # l'erreur remonte au modèle, pas en 500

    def test_la_base_indisponible_n_empeche_pas_de_repondre(self, connecte, monkeypatch):
        """Les consignes sont un confort : leur perte ne doit pas faire taire l'agent."""
        async def casse():
            raise RuntimeError("base injoignable")

        monkeypatch.setattr(sys.modules["agent.memory"], "consignes_actives", casse)
        bloc = asyncio.run(sys.modules["agent.brain"].bloc_consignes())
        assert bloc == ""


# ═════════════════════════════════════════════════════════════════════════
# Fournisseurs de modèle
# ═════════════════════════════════════════════════════════════════════════

class TestFournisseursLLM:
    def test_une_cle_absente_est_signalee_avant_tout_appel(self, env_propre, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        importlib.reload(sys.modules["agent.llm"])
        with pytest.raises(sys.modules["agent.llm"].ErreurLLM, match="ANTHROPIC_API_KEY"):
            sys.modules["agent.llm"].obtenir_client("anthropic")

    def test_une_erreur_de_configuration_donne_un_message_au_client_pas_un_500(
            self, connecte, monkeypatch):
        cerveau = sys.modules["agent.brain"]
        llm = sys.modules["agent.llm"]

        def refuse(*a, **k):
            raise llm.ErreurLLM("clé manquante")

        monkeypatch.setattr(cerveau, "client_llm", refuse)
        texte, vraie = asyncio.run(cerveau.generer_reponse("Bonjour", []))
        assert vraie is False and texte == cerveau.message_erreur()

    def test_une_panne_reseau_donne_aussi_un_message_propre(self, connecte, monkeypatch):
        cerveau = sys.modules["agent.brain"]

        class ClientCasse:
            async def converser(self, **k):
                raise ConnectionError("réseau coupé")

        monkeypatch.setattr(cerveau, "client_llm", lambda: ClientCasse())
        texte, vraie = asyncio.run(cerveau.generer_reponse("Bonjour", []))
        assert vraie is False and texte == cerveau.message_erreur()

    def test_une_reponse_sans_texte_declenche_le_message_de_repli(self, connecte, monkeypatch):
        cerveau = sys.modules["agent.brain"]
        llm = sys.modules["agent.llm"]

        class ClientMuet:
            async def converser(self, **k):
                return llm.Reponse(texte="", tokens_entree=10, tokens_sortie=0, tours=1)

        monkeypatch.setattr(cerveau, "client_llm", lambda: ClientMuet())
        texte, vraie = asyncio.run(cerveau.generer_reponse("Bonjour", []))
        assert vraie is False and texte == cerveau.message_incompris()

    def test_la_boucle_d_outils_est_bornee(self, connecte, monkeypatch):
        cerveau = sys.modules["agent.brain"]
        llm = sys.modules["agent.llm"]

        class ClientEnBoucle:
            async def converser(self, **k):
                return llm.Reponse(texte="", tours=k["max_tours"])

        monkeypatch.setattr(cerveau, "client_llm", lambda: ClientEnBoucle())
        texte, vraie = asyncio.run(cerveau.generer_reponse("Bonjour", []))
        assert vraie is False and texte == cerveau.message_erreur()

    def test_le_numero_n_est_jamais_transmis_au_modele_par_le_client(self, connecte, monkeypatch):
        """
        Le téléphone doit venir du webhook. Un modèle qui en fournirait un autre
        doit être écrasé, sinon une injection permet d'agir au nom d'autrui.
        """
        cerveau = sys.modules["agent.brain"]
        llm = sys.modules["agent.llm"]
        captures = {}

        class ClientCurieux:
            async def converser(self, **k):
                await k["executer"]("enregistrer_demande", {
                    "nom_client": "Pirate", "details": "vol",
                    "date_souhaitee": "2026-12-01 10:00",
                    "telephone": "33699999999"})     # tentative d'usurpation
                return llm.Reponse(texte="fait")

        async def espion(nom, arguments):
            captures.update(arguments)
            return "ok"

        monkeypatch.setattr(cerveau, "client_llm", lambda: ClientCurieux())
        monkeypatch.setattr(cerveau, "executer_outil", espion)
        asyncio.run(cerveau.generer_reponse("Bonjour", [], telephone="33600000000"))
        assert captures["telephone"] == "33600000000"


# ═════════════════════════════════════════════════════════════════════════
# Rétention et effacement (RGPD)
# ═════════════════════════════════════════════════════════════════════════

class TestRGPD:
    def test_les_messages_trop_anciens_sont_purges(self, env_propre, monkeypatch):
        monkeypatch.setenv("RETENTION_JOURS", "30")
        importlib.reload(sys.modules["agent.memory"])
        memoire = sys.modules["agent.memory"]

        async def scenario():
            await memoire.initialiser_base()
            from datetime import datetime, timedelta, timezone
            async with memoire.Session() as s:
                s.add(memoire.Message(
                    telephone="336", role="user", contenu="vieux",
                    cree_le=datetime.now(timezone.utc) - timedelta(days=90)))
                s.add(memoire.Message(telephone="336", role="user", contenu="recent"))
                await s.commit()
            efface = await memoire.purger_donnees_expirees()
            restants = await memoire.obtenir_historique("336")
            return efface, restants

        efface, restants = asyncio.run(scenario())
        assert efface == 1
        assert [m["content"] for m in restants] == ["recent"]

    def test_une_retention_nulle_desactive_la_purge(self, env_propre, monkeypatch):
        monkeypatch.setenv("RETENTION_JOURS", "0")
        importlib.reload(sys.modules["agent.memory"])
        memoire = sys.modules["agent.memory"]

        async def scenario():
            await memoire.initialiser_base()
            return await memoire.purger_donnees_expirees()

        assert asyncio.run(scenario()) == 0

    def test_le_contenu_des_messages_n_est_pas_journalise_par_defaut(self, env_propre):
        securite = sys.modules["agent.securite"]
        r = securite.masquer_contenu("Mon numéro de carte est 4970 1234 5678")
        assert "4970" not in r and "carte" not in r

    def test_l_identifiant_masque_ne_permet_pas_de_remonter_au_numero(self, env_propre):
        securite = sys.modules["agent.securite"]
        masque = securite.masquer_identifiant("33612345678")
        assert "3361234567" not in masque
        # Deux sels différents donnent deux empreintes différentes : pas de
        # table précalculée réutilisable d'une installation à l'autre.
        import hashlib
        brut = hashlib.sha256(b"33612345678").hexdigest()[:8]
        assert brut not in masque
