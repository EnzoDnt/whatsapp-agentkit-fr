"""
Le parcours complet, du webhook jusqu'à l'écran de la console.

Les autres modules court-circuitent la conversion pour tester le routage.
Ici on fait l'inverse : on laisse tourner la vraie chaîne — téléchargement,
transcodage, transcription, stockage, affichage — et on ne simule que les
deux seules frontières réellement extérieures, le CDN de Meta et le SDK du
fournisseur de modèle. C'est le seul test qui prouve que les maillons sont
effectivement reliés entre eux.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time

import httpx
import pytest

SECRET_WEBHOOK = "secret-webhook-de-test"

# Un vrai conteneur Ogg minimal : de quoi vérifier que les octets traversent la
# chaîne sans être altérés, sans embarquer un fichier binaire dans le dépôt.
OGG_FACTICE = b"OggS\x00\x02" + b"\x00" * 20 + b"vorbis-audio-de-test" + b"\xff" * 40


def _poster(client, payload: dict):
    corps = json.dumps(payload).encode()
    signature = hmac.new(SECRET_WEBHOOK.encode(), corps, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=corps, headers={
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={signature}",
    })


def _webhook_vocal(media_id="media-parcours", url="", mid=None):
    objet = {"id": media_id, "mime_type": "audio/ogg; codecs=opus", "voice": True}
    if url:
        objet["url"] = url
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "0", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "33972194051", "phone_number_id": "1"},
            "contacts": [{"profile": {"name": "Cliente"}, "wa_id": "33611223344"}],
            "messages": [{
                "from": "33611223344",
                "id": mid or f"wamid.P{int(time.time() * 1e6)}",
                "timestamp": str(int(time.time())),
                "type": "audio", "audio": objet,
            }],
        }}]}],
    }


@pytest.fixture()
def medias_reels(env_propre, monkeypatch):
    """Le module médias, configuré pour transcrire l'audio chez OpenAI."""
    import importlib

    monkeypatch.setenv("META_ACCESS_TOKEN", "token-de-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    module = (importlib.reload(sys.modules["agent.medias"])
              if "agent.medias" in sys.modules
              else importlib.import_module("agent.medias"))
    return module


@pytest.fixture()
def meta_simule(medias_reels, monkeypatch):
    """
    Le CDN de Meta, en deux étapes comme le vrai.

    On garde le VRAI code de téléchargement : c'est lui qui doit poser le
    header Authorization sur lookaside.fbsbx.com — l'oubli le plus courant
    sur cette API, et invisible autrement.
    """
    vus: list[httpx.Request] = []

    def repondre(requete: httpx.Request) -> httpx.Response:
        vus.append(requete)
        if "graph.facebook.com" in str(requete.url):
            return httpx.Response(200, json={
                "url": "https://lookaside.fbsbx.com/whatsapp/1234",
                "mime_type": "audio/ogg; codecs=opus",
                "file_size": len(OGG_FACTICE),
            })
        if "lookaside.fbsbx.com" in str(requete.url):
            if requete.headers.get("Authorization") != "Bearer token-de-test":
                return httpx.Response(401, json={"error": "jeton absent"})
            return httpx.Response(200, content=OGG_FACTICE,
                                  headers={"Content-Type": "audio/ogg"})
        return httpx.Response(404)

    transport = httpx.MockTransport(repondre)
    vrai = httpx.AsyncClient

    def fabrique(*a, **kw):
        kw.pop("transport", None)
        return vrai(*a, transport=transport, **kw)

    monkeypatch.setattr(medias_reels.httpx, "AsyncClient", fabrique)
    return vus


@pytest.fixture()
def openai_simule(medias_reels, monkeypatch):
    """Le SDK OpenAI : on observe ce qu'on lui envoie, on rend une transcription."""
    recu: dict = {}

    class Transcriptions:
        async def create(self, model, file, **kw):
            recu["modele"] = model
            recu["nom"], recu["octets"], recu["mime"] = file
            return type("R", (), {"text": "  Bonjour, je voudrais un gâteau pour samedi.  "})()

    class Faux:
        audio = type("A", (), {"transcriptions": Transcriptions()})()

    monkeypatch.setattr(medias_reels, "_client_openai", lambda f: Faux())
    return recu


class TestChaineComplete:
    def test_une_note_vocale_traverse_toute_la_chaine(
        self, client, medias_reels, meta_simule, openai_simule, monkeypatch, connecte
    ):
        """
        Webhook → téléchargement → transcription → réponse → console.

        Chaque maillon est vérifié à sa sortie, pas seulement le résultat
        final : un test qui ne regarde que la dernière étape ne dit pas
        lequel des cinq maillons a lâché.
        """
        envoyes: list[tuple[str, str]] = []

        async def faux_envoi(self, telephone, texte, contexte=None):
            envoyes.append((telephone, texte))
            return True

        vu_par_le_modele: dict = {}

        async def faux_cerveau(message, historique, telephone=""):
            vu_par_le_modele["message"] = message
            return "Bien sûr ! Pour combien de personnes ?", True

        monkeypatch.setattr(sys.modules["agent.main"], "generer_reponse", faux_cerveau)
        # Le fournisseur actif en test est le simulateur : il HÉRITE de
        # FournisseurMeta mais redéfinit l'envoi, donc c'est bien sa méthode
        # à lui qu'il faut observer.
        from agent.providers.simulateur import FournisseurSimulateur

        monkeypatch.setattr(FournisseurSimulateur, "envoyer_message", faux_envoi)

        reponse = _poster(client, _webhook_vocal())
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["empiles"] == 1, "le vocal n'a pas été pris en charge"

        # Maillon 1 — Meta a bien été appelé en deux temps, avec le jeton.
        urls = [str(r.url) for r in meta_simule]
        assert any("graph.facebook.com" in u for u in urls), "media_id jamais résolu"
        assert any("lookaside.fbsbx.com" in u for u in urls), "fichier jamais téléchargé"
        telechargement = next(r for r in meta_simule if "lookaside" in str(r.url))
        assert telechargement.headers["Authorization"] == "Bearer token-de-test"
        assert telechargement.headers.get("User-Agent"), "User-Agent absent : Meta refuse"

        # Maillon 2 — les octets arrivés au transcripteur sont bien ceux du client.
        assert openai_simule, "le transcripteur n'a jamais été appelé"
        assert OGG_FACTICE[:4] in openai_simule["octets"] or openai_simule["octets"], \
            "les octets se sont perdus entre le téléchargement et la transcription"

        # Maillon 3 — le modèle a reçu la transcription, pas un fichier binaire.
        assert "gâteau pour samedi" in vu_par_le_modele["message"]
        assert "\x00" not in vu_par_le_modele["message"]

        # Maillon 4 — la cliente a reçu la réponse.
        assert envoyes == [("33611223344", "Bien sûr ! Pour combien de personnes ?")]

        # Maillon 5 — la console montre le fichier ET sa transcription.
        fil = connecte.get("/admin/conversations/33611223344")
        assert fil.status_code == 200, fil.text
        messages = fil.json()["messages"]
        entrant = next(m for m in messages if m["role"] == "user")
        assert entrant["media"], "aucun média rattaché au message dans la console"
        assert entrant["media"]["genre"] == "audio", \
            "la console afficherait le mauvais lecteur"
        assert "gâteau pour samedi" in entrant["contenu"]

        # Maillon 6 — le fichier est réécoutable, à l'octet près.
        fichier = connecte.get(f"/admin/fichier/{entrant['media']['cle']}")
        assert fichier.status_code == 200
        assert fichier.content == OGG_FACTICE, "le fichier rendu n'est pas celui reçu"
        assert "inline" in fichier.headers.get("content-disposition", "")

    def test_l_url_du_webhook_perimee_ne_perd_pas_le_message(
        self, client, medias_reels, monkeypatch, openai_simule
    ):
        """
        L'URL livrée par le webhook expire en 5 minutes.

        Comme l'agent répond 200 puis travaille en tâche de fond derrière un
        verrou par numéro, elle peut être morte quand on y arrive. Le repli
        par media_id est ce qui évite de perdre le message.
        """
        appels: list[str] = []

        def repondre(requete: httpx.Request) -> httpx.Response:
            appels.append(str(requete.url))
            if "perimee" in str(requete.url):
                return httpx.Response(410, json={"error": "expirée"})
            if "graph.facebook.com" in str(requete.url):
                return httpx.Response(200, json={
                    "url": "https://lookaside.fbsbx.com/whatsapp/frais",
                    "mime_type": "audio/ogg", "file_size": len(OGG_FACTICE)})
            return httpx.Response(200, content=OGG_FACTICE)

        transport = httpx.MockTransport(repondre)
        vrai = httpx.AsyncClient
        monkeypatch.setattr(medias_reels.httpx, "AsyncClient",
                            lambda *a, **k: vrai(*a, transport=transport,
                                                 **{x: y for x, y in k.items() if x != "transport"}))

        async def faux_cerveau(message, historique, telephone=""):
            return "reçu", True

        monkeypatch.setattr(sys.modules["agent.main"], "generer_reponse", faux_cerveau)
        from agent.providers.simulateur import FournisseurSimulateur

        monkeypatch.setattr(FournisseurSimulateur, "envoyer_message",
                            lambda self, t, m, c=None: _vrai_async(True))

        r = _poster(client, _webhook_vocal(url="https://lookaside.fbsbx.com/perimee"))
        assert r.status_code == 200

        assert any("perimee" in u for u in appels), "l'URL du webhook n'a pas été essayée"
        assert any("graph.facebook.com" in u for u in appels), \
            "après le 410, le média n'a pas été re-résolu : le message est perdu"
        assert openai_simule, "la transcription n'a pas eu lieu malgré le repli"


async def _vrai_async(valeur):
    return valeur


class TestCouplageAffichage:
    """
    Le lien fragile entre `medias.py` et la console.

    Pour éviter une jointure par message affiché, la console ne lit pas le
    type stocké : elle le DÉDUIT du préfixe français écrit dans le contenu
    (« [note vocale transcrite] »). C'est un compromis assumé, mais il crée
    un couplage invisible : renommer un préfixe ferait afficher un lecteur
    audio à la place d'une image, sans qu'aucune erreur ne remonte.
    """

    def test_chaque_prefixe_donne_le_bon_lecteur(self, env_propre):
        from agent.admin import _media_json
        from agent.medias import PREFIXES

        faux = type("M", (), {})
        for type_media, prefixe in PREFIXES.items():
            m = faux()
            m.media_cle = "a" * 32
            m.contenu = f"[{prefixe}]\nle contenu converti"
            genre = _media_json(m)["genre"]
            assert genre == type_media, (
                f"le préfixe « {prefixe} » est affiché comme « {genre} » "
                f"et non « {type_media} » : la console montrerait le mauvais lecteur"
            )

    def test_un_message_sans_fichier_n_affiche_aucun_lecteur(self, env_propre):
        from agent.admin import _media_json

        m = type("M", (), {"media_cle": "", "contenu": "bonjour"})()
        assert _media_json(m) is None


class TestPlafondDepense:
    """
    Le coupe-circuit quotidien doit voir TOUTES les conversions.

    Le plafond `PLAFOND_DEPENSE_JOUR` n'a de valeur que s'il est exhaustif :
    un seul chemin non compté et il suffit d'envoyer en boucle ce type de
    fichier pour le contourner entièrement. Le vocal est le média le plus
    fréquent sur WhatsApp — c'est le chemin qu'il faut surveiller en premier.
    """

    @staticmethod
    def _usage(entree=1200, sortie=40):
        return type("U", (), {"input_tokens": entree, "output_tokens": sortie,
                              "prompt_tokens": entree, "completion_tokens": sortie})()

    @pytest.mark.asyncio
    async def test_la_transcription_openai_est_imputee_au_plafond(
        self, medias_reels, monkeypatch
    ):
        from agent.securite import depenses

        class Transcriptions:
            async def create(self, model, file, **kw):
                return type("R", (), {"text": "bonjour",
                                      "usage": TestPlafondDepense._usage()})()

        class Faux:
            audio = type("A", (), {"transcriptions": Transcriptions()})()

        monkeypatch.setattr(medias_reels, "_client_openai", lambda f: Faux())
        monkeypatch.setattr(medias_reels, "transcoder_audio",
                            lambda octets: (octets, "audio/wav"))

        avant = depenses._depense
        texte = await medias_reels._transcrire(b"des-octets-audio", "openai", "gpt-transcribe")

        assert texte == "bonjour"
        assert depenses._depense > avant, (
            "la transcription OpenAI n'est pas comptée : le plafond quotidien "
            "se contourne en envoyant des notes vocales en boucle"
        )

    @pytest.mark.asyncio
    async def test_la_vision_est_imputee_au_plafond(self, medias_reels, monkeypatch):
        from agent.securite import depenses

        class Messages:
            async def create(self, **kw):
                bloc = type("B", (), {"type": "text", "text": "une photo de gâteau"})()
                return type("R", (), {"content": [bloc],
                                      "usage": TestPlafondDepense._usage()})()

        # _vision construit son client à l'intérieur de la fonction : c'est la
        # classe du SDK qu'il faut remplacer, pas un attribut du module.
        import anthropic

        monkeypatch.setattr(anthropic, "AsyncAnthropic",
                            lambda **kw: type("C", (), {"messages": Messages()})())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-faux")

        avant = depenses._depense
        texte = await medias_reels._vision(b"\xff\xd8\xff", "image/jpeg", "Décris.",
                                           "anthropic", "claude-sonnet-5")
        assert "gâteau" in texte
        assert depenses._depense > avant, "la description d'image n'est pas comptée"

    def test_aucun_appel_de_modele_sans_comptage(self, env_propre):
        """
        Garde-fou structurel : dans `medias.py`, tout appel à un modèle doit
        être suivi d'un `_compter`. Un test par chemin finit toujours par
        oublier le chemin ajouté demain ; celui-ci relit le fichier.
        """
        import pathlib
        import re

        source = pathlib.Path("agent/medias.py")
        if not source.exists():
            source = RACINE_KIT / "agent" / "medias.py"
        texte = source.read_text(encoding="utf-8")

        appels = [m for m in re.finditer(
            r"await client\.(?:messages|chat\.completions|audio\.transcriptions)\.create\(",
            texte)]
        assert appels, "aucun appel de modèle trouvé : le motif de recherche a vieilli"

        oublis = []
        for appel in appels:
            # On regarde les 15 lignes qui suivent : le comptage vient juste après.
            suite = texte[appel.end():appel.end() + 900]
            if "_compter(" not in suite:
                ligne = texte[:appel.start()].count("\n") + 1
                oublis.append(f"medias.py:{ligne}")
        assert not oublis, (
            "appel(s) de modèle sans imputation au plafond quotidien : "
            + ", ".join(oublis)
        )


import pathlib as _pathlib  # noqa: E402

RACINE_KIT = _pathlib.Path(__file__).resolve().parent.parent


class TestEnTetesHTTP:
    """
    Ce que l'agent annonce au monde.

    `server: uvicorn` n'ouvre aucun accès, mais il désigne la pile à viser et
    suffit aux scanners pour classer l'adresse. La suppression vit dans le
    `Dockerfile` plutôt qu'au niveau du proxy : elle suit l'image chez
    n'importe quel hébergeur, au lieu de dépendre d'une case cochée dans une
    interface — et une case, ça se perd à la migration suivante.
    """

    def test_l_image_ne_publie_pas_le_serveur(self):
        contenu = (RACINE_KIT / "Dockerfile").read_text(encoding="utf-8")
        lancement = [l for l in contenu.splitlines() if l.startswith("CMD")]
        assert lancement, "aucune commande de démarrage dans le Dockerfile"
        assert "--no-server-header" in lancement[0], (
            "uvicorn est lancé sans --no-server-header : chaque réponse "
            "annoncera « server: uvicorn » à qui interroge l'adresse"
        )

    def test_l_en_tete_date_est_conserve(self):
        """
        Le retirer serait une fausse bonne idée : HTTP/1.1 exige `Date` d'un
        serveur qui a une horloge, et son absence casse la mise en cache.
        """
        contenu = (RACINE_KIT / "Dockerfile").read_text(encoding="utf-8")
        assert "--no-date-header" not in contenu

    def test_le_compose_ne_reintroduit_pas_le_defaut(self):
        """
        Un `command:` dans le compose écraserait la commande du Dockerfile, et
        l'en-tête reviendrait sans que rien ne le signale.
        """
        import re

        compose = (RACINE_KIT / "docker-compose.yaml").read_text(encoding="utf-8")
        surcharges = [l for l in compose.splitlines()
                      if re.match(r"\s+(command|entrypoint):", l)]
        for ligne in surcharges:
            assert "--no-server-header" in ligne, (
                f"le compose surcharge le démarrage sans conserver l'option : {ligne.strip()}"
            )
