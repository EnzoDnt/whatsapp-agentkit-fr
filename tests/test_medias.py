"""
Réception des médias, de bout en bout.

Même règle que le reste de la suite : on poste un vrai webhook signé et on
observe ce qui sort. Seuls les appels réseau réellement externes — Meta pour le
téléchargement, le fournisseur de modèle pour la conversion — sont simulés.
Tester les fonctions isolément ne prouverait ni le routage, ni la signature, ni
l'escalade, c'est-à-dire précisément là où les régressions se logent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time

import pytest

SECRET_WEBHOOK = "secret-webhook-de-test"


# ── Fabrication des payloads ─────────────────────────────────────────────


def payload_media(type_msg: str, *, media_id: str = "media-123", mime: str = "",
                  legende: str = "", nom_fichier: str = "", voice: bool = False,
                  url: str = "", mid: str | None = None,
                  expediteur: str = "33600000000") -> dict:
    """Webhook Meta portant un média, au format réel."""
    objet: dict = {"id": media_id}
    if mime:
        objet["mime_type"] = mime
    if url:
        objet["url"] = url
    if legende:
        objet["caption"] = legende
    if nom_fichier:
        objet["filename"] = nom_fichier
    if voice:
        objet["voice"] = True

    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "0", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "33972194051", "phone_number_id": "1"},
            "contacts": [{"profile": {"name": "Client"}, "wa_id": expediteur}],
            "messages": [{
                "from": expediteur,
                "id": mid or f"wamid.M{int(time.time() * 1e6)}",
                "timestamp": str(int(time.time())),
                "type": type_msg,
                type_msg: objet,
            }],
        }}]}],
    }


def poster(client, payload: dict):
    corps = json.dumps(payload).encode()
    signature = hmac.new(SECRET_WEBHOOK.encode(), corps, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=corps, headers={
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={signature}",
    })


@pytest.fixture()
def medias(env_propre, monkeypatch):
    """Module medias rechargé dans l'environnement de test."""
    import importlib

    monkeypatch.setenv("META_ACCESS_TOKEN", "token-de-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "goog-test")
    if "agent.medias" in sys.modules:
        return importlib.reload(sys.modules["agent.medias"])
    return importlib.import_module("agent.medias")


@pytest.fixture()
def escalades(env_propre):
    """Lit les escalades créées, pour vérifier le filet de sécurité."""
    async def lire():
        from sqlalchemy import select

        from agent.memory import Escalade, Session

        async with Session() as session:
            r = await session.execute(select(Escalade))
            return list(r.scalars())

    return lire


# ═════════════════════════════════════════════════════════════════════════
# Bout en bout, par type de média
# ═════════════════════════════════════════════════════════════════════════


class TestBoutEnBout:
    def _simuler(self, monkeypatch, medias, texte_produit):
        """Court-circuite téléchargement et conversion, garde tout le reste."""
        async def faux_convertir(msg):
            return texte_produit

        monkeypatch.setattr(sys.modules["agent.medias"], "convertir_en_texte", faux_convertir)

    def test_une_note_vocale_obtient_une_reponse(self, client, medias, monkeypatch,
                                                 cerveau_simule):
        """
        Le cas qui motive tout ce module.

        Avant, une note vocale ne recevait rien : pas une erreur, le silence.
        """
        self._simuler(monkeypatch, medias,
                      "[note vocale transcrite]\nBonjour, je voudrais un gâteau samedi.")
        vus = []

        async def capter(message, historique, telephone=""):
            vus.append(message)
            return ("Bien sûr, pour combien de personnes ?", True)

        monkeypatch.setattr(sys.modules["agent.main"], "generer_reponse", capter)

        r = poster(client, payload_media("audio", mime="audio/ogg; codecs=opus", voice=True))
        assert r.status_code == 200
        assert r.json()["empiles"] == 1
        # La transcription est bien ce que le modèle a reçu.
        assert vus and "gâteau samedi" in vus[0]

    def test_la_legende_d_une_image_accompagne_la_description(self, client, medias,
                                                              monkeypatch):
        """La légende porte souvent l'intention réelle : « c'est ce modèle-là »."""
        vus = []

        async def capter(message, historique, telephone=""):
            vus.append(message)
            return ("Je regarde ça.", True)

        monkeypatch.setattr(sys.modules["agent.main"], "generer_reponse", capter)
        self._simuler(monkeypatch, medias,
                      "[image reçue]\nUn entremets au chocolat.\n"
                      "[message accompagnant le fichier] c'est ce modèle que je veux")

        poster(client, payload_media("image", mime="image/jpeg",
                                     legende="c'est ce modèle que je veux"))
        assert vus and "c'est ce modèle que je veux" in vus[0]

    def test_un_document_pdf_est_transmis_au_modele(self, client, medias, monkeypatch):
        vus = []

        async def capter(message, historique, telephone=""):
            vus.append(message)
            return ("Bien reçu.", True)

        monkeypatch.setattr(sys.modules["agent.main"], "generer_reponse", capter)
        self._simuler(monkeypatch, medias, "[document reçu « devis.pdf »]\nDevis n°42 : 480 EUR")

        poster(client, payload_media("document", mime="application/pdf", nom_fichier="devis.pdf"))
        assert vus and "480 EUR" in vus[0]


# ═════════════════════════════════════════════════════════════════════════
# Téléchargement — les trois pièges de l'API Meta
# ═════════════════════════════════════════════════════════════════════════


class TestTelechargement:
    @pytest.mark.asyncio
    async def test_le_jeton_et_le_user_agent_sont_envoyes(self, medias, monkeypatch):
        """
        lookaside.fbsbx.com ressemble à un CDN public : il ne l'est pas.

        Sans Authorization, la requête échoue ; sans User-Agent explicite, elle
        est parfois rejetée. Ce sont les deux erreurs classiques sur cette API.
        """
        vues = []

        class FausseReponse:
            status_code = 200
            content = b"des-octets"

            def raise_for_status(self):
                pass

            def json(self):
                return {"url": "https://lookaside.fbsbx.com/x", "mime_type": "audio/ogg",
                        "file_size": 10}

        class FauxClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None, **k):
                vues.append((url, headers or {}))
                return FausseReponse()

        monkeypatch.setattr(medias.httpx, "AsyncClient", FauxClient)

        octets, _ = await medias.telecharger("media-123", "https://lookaside.fbsbx.com/direct")
        assert octets == b"des-octets"
        entetes = vues[0][1]
        assert entetes.get("Authorization") == "Bearer token-de-test"
        assert entetes.get("User-Agent")

    @pytest.mark.asyncio
    async def test_url_du_webhook_perimee_puis_reresolution(self, medias, monkeypatch):
        """
        L'URL du webhook expire en 5 minutes.

        L'agent répond 200 puis travaille en tâche de fond derrière un verrou
        par numéro : trois vocaux d'affilée suffisent à la périmer. Seul le
        media_id est durable, d'où la re-résolution.
        """
        appels = []

        class Reponse:
            def __init__(self, code, contenu=b"", data=None):
                self.status_code = code
                self.content = contenu
                self._d = data or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise medias.httpx.HTTPStatusError("expirée", request=None, response=None)

            def json(self):
                return self._d

        class FauxClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None, **k):
                appels.append(url)
                if url.endswith("/perimee"):
                    return Reponse(401)                       # l'URL du webhook est morte
                if "graph.facebook.com" in url:
                    return Reponse(200, data={"url": "https://lookaside.fbsbx.com/fraiche",
                                              "mime_type": "audio/ogg", "file_size": 5})
                return Reponse(200, contenu=b"octets-frais")

        monkeypatch.setattr(medias.httpx, "AsyncClient", FauxClient)

        octets, _ = await medias.telecharger("media-123", "https://lookaside.fbsbx.com/perimee")
        assert octets == b"octets-frais"
        assert any("graph.facebook.com" in u for u in appels), "la re-résolution n'a pas eu lieu"

    @pytest.mark.asyncio
    async def test_un_fichier_trop_lourd_n_est_pas_telecharge(self, medias, monkeypatch):
        """Meta autorise 100 Mo pour un document : bien au-delà d'une requête modèle."""
        telecharges = []

        class Reponse:
            status_code = 200
            content = b""

            def raise_for_status(self):
                pass

            def json(self):
                return {"url": "https://lookaside.fbsbx.com/gros", "mime_type": "application/pdf",
                        "file_size": 90 * 1024 * 1024}

        class FauxClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None, **k):
                if "lookaside" in url:
                    telecharges.append(url)
                return Reponse()

        monkeypatch.setattr(medias.httpx, "AsyncClient", FauxClient)

        with pytest.raises(medias.MediaIndisponible) as e:
            await medias.telecharger("media-gros")
        assert "Mo" in str(e.value)
        assert not telecharges, "le fichier a été téléchargé malgré le plafond"


# ═════════════════════════════════════════════════════════════════════════
# Le filet : tout échec escalade vers un humain
# ═════════════════════════════════════════════════════════════════════════


class TestEscalade:
    @pytest.mark.asyncio
    async def test_type_desactive_escalade_et_conserve_le_message(self, client, medias,
                                                                  monkeypatch, escalades):
        """Un média non traité ne disparaît pas : un humain est prévenu."""
        medias.enregistrer_routage({"video": "escalade"})

        r = poster(client, payload_media("video", mime="video/mp4"))
        assert r.json()["empiles"] == 1

        liste = await escalades()
        assert len(liste) == 1
        assert "vidéo" in liste[0].motif.lower() or "désactiv" in liste[0].motif.lower()

    @pytest.mark.asyncio
    async def test_l_agent_se_met_en_pause_apres_une_escalade_media(self, client, medias,
                                                                    monkeypatch):
        """
        Sans mise en pause, l'agent répondrait au message suivant comme si de
        rien n'était, alors qu'un humain a repris la conversation.
        """
        from agent.memory import conversation_en_pause

        medias.enregistrer_routage({"video": "escalade"})
        poster(client, payload_media("video", mime="video/mp4"))
        assert await conversation_en_pause("33600000000") is True

    @pytest.mark.asyncio
    async def test_un_echec_de_conversion_escalade(self, client, medias, monkeypatch,
                                                   escalades):
        async def echoue(msg):
            raise medias.MediaIndisponible("Transcription indisponible.")

        monkeypatch.setattr(sys.modules["agent.medias"], "convertir_en_texte", echoue)
        poster(client, payload_media("audio", mime="audio/ogg", voice=True))

        liste = await escalades()
        assert len(liste) == 1
        assert "Transcription indisponible." in liste[0].motif

    @pytest.mark.asyncio
    async def test_un_autocollant_escalade_au_lieu_de_disparaitre(self, client, medias,
                                                                  escalades):
        poster(client, payload_media("sticker", mime="image/webp"))
        liste = await escalades()
        assert len(liste) == 1


# ═════════════════════════════════════════════════════════════════════════
# Routage : capacités et disponibilité
# ═════════════════════════════════════════════════════════════════════════


class TestRoutage:
    def test_anthropic_ne_peut_pas_traiter_l_audio(self, medias):
        """
        L'API Messages d'Anthropic accepte texte, images et PDF — pas l'audio.

        Comme anthropic est le fournisseur par défaut du kit, la case doit être
        grisée dans la console au lieu d'échouer au premier vocal reçu.
        """
        assert medias.possible("audio", "anthropic") is False
        assert medias.possible("image", "anthropic") is True

    def test_un_fournisseur_sans_cle_est_indisponible(self, medias, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert medias.possible("video", "google") is False

    def test_le_tableau_explique_chaque_case_grisee(self, medias):
        """Une case grisée sans explication est une impasse pour l'utilisateur."""
        for ligne in medias.tableau_routage()["lignes"]:
            for case in ligne["cases"]:
                if not case["possible"]:
                    assert case["raison"], f"{ligne['type']}/{case['fournisseur']} sans raison"

    def test_l_escalade_est_toujours_disponible(self, medias):
        for t in medias.TYPES_MEDIA:
            assert medias.possible(t, "escalade") is True

    def test_un_choix_impossible_est_refuse(self, medias):
        with pytest.raises(medias.MediaIndisponible):
            medias.enregistrer_routage({"audio": "anthropic"})

    def test_le_choix_enregistre_est_relu(self, medias):
        medias.enregistrer_routage({"image": "openai"})
        assert medias.fournisseur_pour("image") == "openai"

    def test_l_audio_bascule_sur_un_fournisseur_capable(self, medias, monkeypatch):
        """Avec Anthropic en principal, l'audio doit trouver OpenAI tout seul."""
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        assert medias.fournisseur_pour("audio") in ("openai", "google")


# ═════════════════════════════════════════════════════════════════════════
# La console
# ═════════════════════════════════════════════════════════════════════════


class TestConsole:
    def test_le_tableau_exige_une_session(self, client):
        assert client.get("/admin/medias").status_code == 401

    def test_le_tableau_est_lisible_une_fois_connecte(self, connecte, medias):
        d = connecte.get("/admin/medias").json()
        assert {c["id"] for c in d["colonnes"]} == set(medias.CAPACITES)
        assert len(d["lignes"]) == len(medias.TYPES_MEDIA)

    def test_le_catalogue_de_modeles_est_protege(self, client):
        assert client.get("/admin/medias/modeles?fournisseur=openai").status_code == 401

    def test_un_fournisseur_inconnu_est_refuse(self, connecte, medias):
        r = connecte.get("/admin/medias/modeles?fournisseur=nimportequoi")
        assert r.status_code == 400

    def test_le_catalogue_ne_renvoie_jamais_une_liste_vide(self, connecte, medias,
                                                          monkeypatch):
        """
        Si l'API du fournisseur est injoignable, on retombe sur les valeurs par
        défaut : l'utilisateur doit toujours pouvoir choisir quelque chose.
        """
        async def injoignable(*a, **k):
            raise medias.httpx.HTTPError("réseau coupé")

        monkeypatch.setattr(medias.httpx.AsyncClient, "get", injoignable, raising=False)
        d = connecte.get("/admin/medias/modeles?fournisseur=openai&type_media=audio").json()
        assert d["modeles"], "aucun modèle proposé alors que le repli devait s'appliquer"

    def test_enregistrer_un_choix_impossible_renvoie_400(self, connecte, medias):
        r = connecte.put("/admin/medias", json={"choix": {"audio": "anthropic"}})
        assert r.status_code == 400

    def test_enregistrer_l_escalade_fonctionne(self, connecte, medias):
        r = connecte.put("/admin/medias", json={"choix": {"video": "escalade"}})
        assert r.status_code == 200
        assert r.json()["choix"]["video"]["fournisseur"] == "escalade"

    def test_choisir_un_modele_precis(self, connecte, medias):
        """Le modèle se choisit par type, pas seulement le fournisseur."""
        r = connecte.put("/admin/medias", json={"choix": {
            "audio": {"fournisseur": "openai", "modele": "gpt-4o-mini-transcribe"}}})
        assert r.status_code == 200
        assert medias.modele_pour("audio", "openai") == "gpt-4o-mini-transcribe"

    def test_l_ancien_format_chaine_reste_lu(self, medias):
        """Une configuration écrite par une version antérieure ne doit pas casser."""
        medias.FICHIER_ROUTAGE.parent.mkdir(exist_ok=True)
        medias.FICHIER_ROUTAGE.write_text("image: openai\n", encoding="utf-8")
        assert medias.fournisseur_pour("image") == "openai"


# ═════════════════════════════════════════════════════════════════════════
# Non-régression
# ═════════════════════════════════════════════════════════════════════════


class TestNonRegression:
    def test_un_message_texte_reste_traite_comme_avant(self, client, cerveau_simule):
        from tests.test_integration import payload_meta

        r = poster(client, payload_meta("Bonjour"))
        assert r.status_code == 200
        assert r.json()["empiles"] == 1

    def test_un_media_rejoue_est_dedoublonne(self, client, medias, monkeypatch):
        """Meta rejoue un événement jusqu'à sept fois : jamais deux traitements."""
        async def faux(msg):
            return "[note vocale transcrite]\nBonjour"

        monkeypatch.setattr(sys.modules["agent.medias"], "convertir_en_texte", faux)

        charge = payload_media("audio", mime="audio/ogg", voice=True, mid="wamid.REJOUE")
        assert poster(client, charge).json()["empiles"] == 1
        assert poster(client, charge).json()["empiles"] == 0

    @pytest.mark.asyncio
    async def test_la_depense_media_compte_dans_le_plafond(self, medias):
        """
        Sans cette imputation, le coupe-circuit ne protège plus rien : quelqu'un
        qui envoie des vidéos en boucle contournerait PLAFOND_DEPENSE_JOUR.

        `_compter` est asynchrone depuis que le cumul est aussi écrit en base :
        le plafond doit survivre à un redéploiement, pas seulement au message
        suivant.
        """
        depenses = sys.modules["agent.securite"].depenses
        avant = depenses.depense_du_jour

        class FausseReponse:
            class usage:
                input_tokens = 100_000
                output_tokens = 1_000

        await medias._compter(FausseReponse(), "claude-sonnet-5")
        assert depenses.depense_du_jour > avant


# ═════════════════════════════════════════════════════════════════════════
# Consultation du fichier dans la console
# ═════════════════════════════════════════════════════════════════════════


class TestConsultation:
    """
    Le fichier lui-même doit rester consultable : une note vocale réduite à sa
    transcription perd l'intonation, et l'humain qui reprend la conversation a
    souvent besoin de l'entendre.
    """

    async def _poser(self, cle_octets=b"OggS\x00\x00"):
        from agent.memory import enregistrer_message, stocker_media

        cle = await stocker_media("33600000000", "audio", cle_octets, "audio/ogg")
        await enregistrer_message(
            "33600000000", "user", "[note vocale transcrite]\nBonjour", media_cle=cle
        )
        return cle

    @pytest.mark.asyncio
    async def test_le_fichier_apparait_dans_la_conversation(self, connecte, env_propre):
        cle = await self._poser()
        d = connecte.get("/admin/conversations/33600000000").json()
        media = d["messages"][0]["media"]
        assert media is not None
        assert media["genre"] == "audio"
        assert media["cle"] == cle

    @pytest.mark.asyncio
    async def test_le_fichier_est_servi_avec_son_type(self, connecte, env_propre):
        cle = await self._poser(b"OggS-contenu-audio")
        r = connecte.get(f"/admin/fichier/{cle}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/ogg")
        assert r.content == b"OggS-contenu-audio"
        # inline : le navigateur lit au lieu de télécharger.
        assert "inline" in r.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_un_fichier_exige_une_session(self, client, env_propre):
        """
        C'est la voix d'une personne, parfois sa photo : aucune URL publique,
        même difficile à deviner.
        """
        cle = await self._poser()
        from fastapi.testclient import TestClient

        with TestClient(client.app) as anonyme:
            assert anonyme.get(f"/admin/fichier/{cle}").status_code == 401

    def test_une_reference_invalide_est_refusee(self, connecte, env_propre):
        for mauvaise in ("../../etc/passwd", "pas-une-cle", "z" * 32):
            r = connecte.get(f"/admin/fichier/{mauvaise}")
            assert r.status_code in (400, 404), mauvaise

    @pytest.mark.asyncio
    async def test_un_message_sans_fichier_n_expose_rien(self, connecte, env_propre):
        from agent.memory import enregistrer_message

        await enregistrer_message("33600000000", "user", "Bonjour")
        d = connecte.get("/admin/conversations/33600000000").json()
        assert d["messages"][0]["media"] is None

    @pytest.mark.asyncio
    async def test_les_fichiers_sont_purges_avec_les_messages(self, env_propre, monkeypatch):
        """
        Conserver la voix d'un client au-delà de son historique n'aurait aucune
        justification : la purge RGPD doit emporter les deux.
        """
        import sys
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select, update

        memoire = sys.modules["agent.memory"]
        # Ce test n'ouvre pas l'application : la base doit être créée à la main.
        await memoire.initialiser_base()
        cle = await self._poser()

        vieux = datetime.now(timezone.utc) - timedelta(days=400)
        async with memoire.Session() as session:
            await session.execute(
                update(memoire.FichierMedia).values(cree_le=vieux)
            )
            await session.execute(update(memoire.Message).values(cree_le=vieux))
            await session.commit()

        await memoire.purger_donnees_expirees()

        async with memoire.Session() as session:
            restants = list((await session.execute(select(memoire.FichierMedia))).scalars())
        assert restants == [], "le fichier a survécu à la purge de son message"


# ═════════════════════════════════════════════════════════════════════════
# Rafraîchissement du fil (garde-fous sur le code de la console)
# ═════════════════════════════════════════════════════════════════════════


class TestRafraichissement:
    """
    Ces vérifications portent sur le JavaScript de la console.

    Elles existent à cause d'un bug réel : le rafraîchissement était court-
    circuité par le sélecteur `audio:not([paused])`. Or « paused » est une
    propriété JavaScript, pas un attribut HTML — `[paused]` ne correspond donc
    à rien, la condition était vraie pour tout lecteur audio, et la conversation
    cessait définitivement de se mettre à jour dès qu'une note vocale y figurait.
    Il fallait recharger la page à la main.
    """

    @staticmethod
    def _console() -> str:
        from pathlib import Path

        return Path("simulateur/admin.html").read_text(encoding="utf-8")

    def test_aucun_selecteur_css_sur_une_propriete_media(self, env_propre):
        """`[paused]`, `[ended]`, `[muted]` en CSS ne matchent jamais rien."""
        code = self._console()
        # On ignore les commentaires, qui documentent justement le piège.
        actif = "\n".join(
            l for l in code.splitlines() if not l.strip().startswith("//")
        )
        for piege in (":not([paused])", "[paused]", ":not([ended])"):
            assert piege not in actif, f"sélecteur CSS invalide sur une propriété : {piege}"

    def test_le_fil_se_rafraichit_dans_la_boucle(self, env_propre):
        code = self._console()
        assert "rafraichirFil()" in code
        # Le rafraîchissement doit être dans le sondage, pas seulement défini.
        boucle = code[code.index("setInterval("):code.index("setInterval(") + 400]
        assert "rafraichirFil" in boucle, "le fil n'est pas rafraîchi par le sondage"

    def test_le_rendu_est_incremental(self, env_propre):
        """
        Réécrire tout le fil à chaque tour couperait la lecture d'un vocal.
        On n'ajoute que les messages nouveaux.
        """
        code = self._console()
        assert "insertAdjacentHTML" in code, "le fil est repeint au lieu d'être complété"
        assert "dernierId" in code

    def test_le_sondage_ne_s_empile_pas(self, env_propre):
        """
        entrer() peut être rappelé après une session expirée puis reconnexion.
        Sans garde, chaque passage ajouterait une minuterie : au bout de
        quelques heures la console interrogerait le serveur plusieurs fois par
        seconde.
        """
        code = self._console()
        assert "clearInterval" in code, "aucune protection contre l'empilement des minuteries"

    def test_l_identifiant_de_message_est_expose_par_l_api(self, connecte, env_propre):
        """Le rendu incrémental compare des identifiants : l'API doit les fournir."""
        import asyncio

        from agent.memory import enregistrer_message

        asyncio.run(enregistrer_message("33600000000", "user", "Bonjour"))
        d = connecte.get("/admin/conversations/33600000000").json()
        assert d["messages"], "aucun message"
        assert isinstance(d["messages"][0].get("id"), int)
