"""Suite d'intégration, seconde partie : contenu, escalade, conformité."""

from __future__ import annotations

import json

import pytest

from tests.test_integration import payload_meta, poster


# ═════════════════════════════════════════════════════════════════════════
# Documents métier
# ═════════════════════════════════════════════════════════════════════════

class TestDocuments:
    def test_les_documents_existants_sont_listes(self, connecte):
        noms = [d["nom"] for d in connecte.get("/admin/documents").json()["documents"]]
        assert "tarifs.md" in noms

    def test_creation_relecture_et_suppression(self, connecte):
        connecte.put("/admin/documents/faq.md", json={"contenu": "# FAQ\nOuvert le mardi."})
        assert connecte.get("/admin/documents/faq.md").json()["contenu"].startswith("# FAQ")
        connecte.delete("/admin/documents/faq.md")
        assert connecte.get("/admin/documents/faq.md").status_code == 404

    @pytest.mark.parametrize("nom", [
        "../../.env", "..%2F..%2Fsecret", "/etc/passwd", ".env", ".ssh", "config.py",
        "script.sh", "page.html", "note.md.exe", "a" * 120 + ".md",
    ])
    def test_les_noms_de_fichiers_dangereux_sont_refuses(self, connecte, nom):
        r = connecte.put(f"/admin/documents/{nom}", json={"contenu": "compromis"})
        assert r.status_code in (400, 404), f"{nom} accepté !"

    def test_un_document_inexistant_renvoie_404(self, connecte):
        assert connecte.get("/admin/documents/jamais-vu.md").status_code == 404

    def test_l_agent_retrouve_ce_qui_est_ecrit_dans_les_documents(self, connecte):
        connecte.put("/admin/documents/promo.md",
                     json={"contenu": "Number cake 8 parts : 45 EUR"})
        import asyncio
        import sys
        resultat = asyncio.run(
            sys.modules["agent.tools"].executer_outil(
                "rechercher_information", {"requete": "number cake"})
        )
        assert "45 EUR" in resultat

    def test_une_recherche_sans_resultat_le_dit_clairement(self, connecte):
        import asyncio
        import sys
        r = asyncio.run(sys.modules["agent.tools"].executer_outil(
            "rechercher_information", {"requete": "hélicoptère"}))
        assert "aucune information" in r.lower()


# ═════════════════════════════════════════════════════════════════════════
# Comportement de l'agent
# ═════════════════════════════════════════════════════════════════════════

class TestPrompt:
    def test_le_prompt_est_lisible_et_modifiable(self, connecte):
        assert "assistant de test" in connecte.get("/admin/prompts").json()["system_prompt"]
        connecte.put("/admin/prompts", json={
            "system_prompt": "Nouveau comportement.", "fallback_message": "Hein ?",
            "error_message": "Panne.", "quota_message": "Trop."})
        assert connecte.get("/admin/prompts").json()["system_prompt"] == "Nouveau comportement."

    def test_un_prompt_vide_est_refuse(self, connecte):
        assert connecte.put("/admin/prompts", json={"system_prompt": "   "}).status_code == 400

    def test_une_sauvegarde_est_conservee_a_chaque_ecriture(self, connecte, env_propre):
        connecte.put("/admin/prompts", json={"system_prompt": "Version deux."})
        sauvegardes = list((env_propre / "config").glob("prompts.*.bak"))
        assert sauvegardes, "aucune sauvegarde du prompt"
        assert "assistant de test" in sauvegardes[0].read_text(encoding="utf-8")

    def test_le_prompt_modifie_est_bien_celui_envoye_au_modele(self, connecte):
        connecte.put("/admin/prompts", json={"system_prompt": "MARQUEUR-UNIQUE-4271"})
        import sys
        assert "MARQUEUR-UNIQUE-4271" in sys.modules["agent.brain"].prompt_systeme()


# ═════════════════════════════════════════════════════════════════════════
# Escalade humaine
# ═════════════════════════════════════════════════════════════════════════

class TestEscalade:
    def _escalader(self, connecte):
        import asyncio
        import sys
        return asyncio.run(sys.modules["agent.tools"].executer_outil("passer_la_main", {
            "motif": "Le client demande un humain",
            "question_equipe": "Peut-on livrer le 15 ?",
            "reponse_proposee": "Oui, livraison possible le 15.",
            "urgence": "haute", "telephone": "33600000000"}))

    def test_l_outil_cree_une_escalade_en_attente(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        self._escalader(connecte)
        e = connecte.get("/admin/escalades").json()["escalades"]
        assert len(e) == 1
        assert e[0]["urgence"] == "haute"
        assert e[0]["reponse_proposee"] == "Oui, livraison possible le 15."

    def test_l_escalade_met_la_conversation_en_pause(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        self._escalader(connecte)
        assert connecte.get("/admin/conversations/33600000000").json()["en_pause"] is True

    def test_valider_le_brouillon_envoie_et_signe_le_message(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        self._escalader(connecte)
        ident = connecte.get("/admin/escalades").json()["escalades"][0]["id"]
        r = connecte.post(f"/admin/escalades/{ident}/repondre", json={
            "texte": "Oui, livraison possible le 15.",
            "brouillon": "Oui, livraison possible le 15."})
        assert r.status_code == 200 and r.json()["brouillon_modifie"] is False
        dernier = connecte.get("/admin/conversations/33600000000").json()["messages"][-1]
        assert dernier["auteur"] == "humain"
        assert dernier["valide_par"] == "test@exemple.fr"

    def test_corriger_le_brouillon_est_detecte(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        self._escalader(connecte)
        ident = connecte.get("/admin/escalades").json()["escalades"][0]["id"]
        r = connecte.post(f"/admin/escalades/{ident}/repondre", json={
            "texte": "Non, pas avant le 18.", "brouillon": "Oui, livraison possible le 15."})
        assert r.json()["brouillon_modifie"] is True

    def test_une_escalade_ne_peut_pas_etre_traitee_deux_fois(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        self._escalader(connecte)
        ident = connecte.get("/admin/escalades").json()["escalades"][0]["id"]
        connecte.post(f"/admin/escalades/{ident}/repondre", json={"texte": "ok"})
        r = connecte.post(f"/admin/escalades/{ident}/repondre", json={"texte": "encore"})
        assert r.status_code == 409

    def test_une_reponse_vide_est_refusee(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        self._escalader(connecte)
        ident = connecte.get("/admin/escalades").json()["escalades"][0]["id"]
        assert connecte.post(f"/admin/escalades/{ident}/repondre",
                             json={"texte": " "}).status_code == 400

    def test_classer_une_escalade_rend_la_main_a_l_agent(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        self._escalader(connecte)
        ident = connecte.get("/admin/escalades").json()["escalades"][0]["id"]
        connecte.post(f"/admin/escalades/{ident}/ignorer")
        assert connecte.get("/admin/conversations/33600000000").json()["en_pause"] is False
        assert connecte.get("/admin/escalades").json()["escalades"] == []

    def test_la_conversation_signale_qu_elle_attend_une_reponse(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        self._escalader(connecte)
        c = connecte.get("/admin/conversations").json()["conversations"][0]
        assert c["attend_reponse"] is True

    def test_une_escalade_inexistante_renvoie_404(self, connecte):
        assert connecte.post("/admin/escalades/9999/repondre",
                             json={"texte": "x"}).status_code == 404


# ═════════════════════════════════════════════════════════════════════════
# Demandes
# ═════════════════════════════════════════════════════════════════════════

class TestDemandes:
    def _creer(self):
        import asyncio
        import sys
        return asyncio.run(sys.modules["agent.tools"].executer_outil("enregistrer_demande", {
            "nom_client": "Donati", "details": "Entremets 10 parts pistache",
            "date_souhaitee": "2026-08-29 10:00", "telephone": "33600000000"}))

    def test_une_demande_enregistree_apparait_dans_la_console(self, connecte):
        self._creer()
        d = connecte.get("/admin/demandes").json()["demandes"]
        assert len(d) == 1 and d[0]["nom_client"] == "Donati"
        assert d[0]["statut"] == "a_traiter"

    def test_le_filtre_par_statut_fonctionne(self, connecte):
        self._creer()
        ident = connecte.get("/admin/demandes").json()["demandes"][0]["id"]
        connecte.patch(f"/admin/demandes/{ident}", json={"statut": "traitee"})
        assert connecte.get("/admin/demandes?statut=a_traiter").json()["demandes"] == []
        assert len(connecte.get("/admin/demandes?statut=traitee").json()["demandes"]) == 1

    def test_un_statut_inconnu_est_refuse(self, connecte):
        self._creer()
        ident = connecte.get("/admin/demandes").json()["demandes"][0]["id"]
        assert connecte.patch(f"/admin/demandes/{ident}",
                              json={"statut": "inventé"}).status_code == 400

    def test_le_numero_est_masque_dans_la_liste(self, connecte):
        self._creer()
        d = connecte.get("/admin/demandes").json()["demandes"][0]
        assert "33600000000" not in d["affichage"]


# ═════════════════════════════════════════════════════════════════════════
# Règles métier vérifiées en code
# ═════════════════════════════════════════════════════════════════════════

class TestReglesMetier:
    def _delai(self, type_prestation, date):
        import asyncio
        import sys
        return asyncio.run(sys.modules["agent.tools"].executer_outil(
            "verifier_delai", {"type_prestation": type_prestation, "date_souhaitee": date}))

    def test_une_date_trop_proche_est_refusee(self, connecte):
        from datetime import datetime, timedelta
        demain = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        assert self._delai("ceremonie", demain).startswith("REFUSÉ")

    def test_une_date_conforme_est_acceptee(self, connecte):
        from datetime import datetime, timedelta
        dans_cinq = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
        assert self._delai("ceremonie", dans_cinq).startswith("ACCEPTÉ")

    def test_une_prestation_sans_delai_accepte_le_jour_meme(self, connecte):
        from datetime import datetime, timedelta
        dans_une_heure = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        assert self._delai("standard", dans_une_heure).startswith("ACCEPTÉ")

    def test_une_date_illisible_ne_fait_pas_planter_l_outil(self, connecte):
        assert "format" in self._delai("ceremonie", "la semaine prochaine").lower()

    def test_un_type_de_prestation_inconnu_ne_bloque_pas(self, connecte):
        from datetime import datetime, timedelta
        demain = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        assert self._delai("inconnu", demain).startswith("ACCEPTÉ")

    def test_un_outil_inexistant_renvoie_une_erreur_lisible(self, connecte):
        import asyncio
        import sys
        r = asyncio.run(sys.modules["agent.tools"].executer_outil("effacer_la_base", {}))
        assert "inconnu" in r.lower()

    def test_des_arguments_invalides_ne_font_pas_perdre_le_tour(self, connecte):
        import asyncio
        import sys
        r = asyncio.run(sys.modules["agent.tools"].executer_outil(
            "verifier_delai", {"parametre_inexistant": 1}))
        assert "argument" in r.lower()


# ═════════════════════════════════════════════════════════════════════════
# Marque du client
# ═════════════════════════════════════════════════════════════════════════

class TestMarque:
    def test_sans_configuration_aucune_marque_n_est_annoncee(self, connecte):
        d = connecte.get("/admin/marque").json()
        assert d["nom"] == "" and d["a_logo"] is False

    def test_le_logo_est_servi_quand_il_existe(self, connecte, env_propre):
        (env_propre / "config" / "logo.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        (env_propre / "config" / "marque.yaml").write_text(
            'nom: "Maison Lorette"\nlogo: "logo.svg"\n', encoding="utf-8")
        assert connecte.get("/admin/marque").json() == {"nom": "Maison Lorette", "a_logo": True}
        r = connecte.get("/admin/logo")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg")

    def test_un_logo_declare_mais_absent_ne_casse_rien(self, connecte, env_propre):
        (env_propre / "config" / "marque.yaml").write_text(
            'nom: "X"\nlogo: "absent.png"\n', encoding="utf-8")
        assert connecte.get("/admin/marque").json()["a_logo"] is False
        assert connecte.get("/admin/logo").status_code == 404


# ═════════════════════════════════════════════════════════════════════════
# Conformité AI Act
# ═════════════════════════════════════════════════════════════════════════

class TestTransparence:
    @pytest.mark.parametrize("mode,attendu", [
        ("discrete", "intelligence artificielle"),
        ("explicite", "assistant automatique"),
    ])
    def test_la_mention_est_ajoutee_au_premier_echange(self, connecte, mode, attendu):
        import sys
        cerveau = sys.modules["agent.brain"]
        avant = cerveau.MODE_TRANSPARENCE
        try:
            cerveau.MODE_TRANSPARENCE = mode
            r = cerveau.appliquer_transparence("Bonjour.", premier_echange=True)
            assert attendu in r.lower()
            assert cerveau.appliquer_transparence("Suite.", premier_echange=False) == "Suite."
        finally:
            cerveau.MODE_TRANSPARENCE = avant

    def test_le_mode_discret_place_la_mention_en_pied(self, connecte):
        import sys
        cerveau = sys.modules["agent.brain"]
        avant = cerveau.MODE_TRANSPARENCE
        try:
            cerveau.MODE_TRANSPARENCE = "discrete"
            r = cerveau.appliquer_transparence("Bonjour.", premier_echange=True)
            assert r.startswith("Bonjour.")
        finally:
            cerveau.MODE_TRANSPARENCE = avant

    def test_chaque_message_porte_son_auteur(self, connecte, cerveau_simule):
        poster(connecte, payload_meta("Bonjour"))
        connecte.post("/admin/conversations/33600000000/repondre", json={"texte": "Salut"})
        messages = connecte.get("/admin/conversations/33600000000").json()["messages"]
        assert {m["auteur"] for m in messages} == {"client", "agent", "humain"}
        assert all(m["auteur"] for m in messages), "un message sans auteur identifié"


# ═════════════════════════════════════════════════════════════════════════
# Simulateur local
# ═════════════════════════════════════════════════════════════════════════

class TestSimulateur:
    def test_le_message_traverse_le_vrai_webhook(self, client, cerveau_simule):
        r = client.post("/simulateur/envoyer", json={"texte": "Bonjour"})
        assert r.status_code == 200
        assert r.json()["reponse_webhook"]["empiles"] == 1

    def test_la_reponse_revient_dans_la_file(self, client, cerveau_simule):
        client.post("/simulateur/envoyer", json={"texte": "Bonjour"})
        messages = client.get("/simulateur/messages?depuis=0").json()["messages"]
        assert len(messages) == 1 and "Bonjour" in messages[0]["texte"]

    def test_un_message_vide_est_refuse(self, client):
        assert client.post("/simulateur/envoyer", json={"texte": "  "}).status_code == 400

    def test_la_reinitialisation_vide_la_conversation(self, client, cerveau_simule):
        client.post("/simulateur/envoyer", json={"texte": "Bonjour"})
        client.post("/simulateur/reinitialiser")
        assert client.get("/simulateur/messages?depuis=0").json()["messages"] == []
