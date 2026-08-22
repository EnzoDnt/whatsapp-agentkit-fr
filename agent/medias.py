"""
Réception des médias : audio, images, vidéos et documents.

Avant ce module, l'agent ignorait tout message qui n'était pas du texte. Un
client envoyait une note vocale — l'usage le plus courant sur WhatsApp — et
n'obtenait aucune réponse. Pas une erreur, pas une excuse : le silence, qui
ressemble à une panne.

Principe retenu : **le média est converti en texte en amont**, puis le flux
normal continue sans rien changer. Trois raisons :

1. Un seul chemin pour les quatre fournisseurs, sans code par modèle.
2. L'humain lit la transcription dans la console : il comprend une conversation
   sans réécouter le vocal, ce qui rend l'escalade réellement utilisable.
3. brain.py, llm.py et memory.py ne bougent pas.

Le routage — quel fournisseur traite quel type — se règle depuis la console
(config/medias.yaml), parce que c'est une décision de coût qui appartient au
client, pas une constante technique.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import yaml

from agent.securite import depenses

logger = logging.getLogger("agentkit")

FICHIER_ROUTAGE = Path("config/medias.yaml")

# Meta rejette parfois les requêtes sans User-Agent explicite vers
# lookaside.fbsbx.com. Ce n'est pas documenté, mais c'est reproductible.
UA = "AgentKit-FR/1.0 (+https://github.com/EnzoDnt/whatsapp-agentkit-fr)"

# ── Ce que chaque fournisseur sait réellement faire ──────────────────────
#
# Anthropic ne lit PAS l'audio : l'API Messages accepte texte, images et PDF,
# c'est tout. Comme anthropic est le fournisseur par défaut du kit, traiter les
# vocaux impose une clé chez un second fournisseur. On ne peut pas le masquer,
# seulement le rendre visible — d'où cette table, qui pilote aussi le tableau
# de la console : une case impossible y est grisée au lieu d'échouer plus tard.
CAPACITES: dict[str, set[str]] = {
    "anthropic": {"image", "document"},
    "openai": {"audio", "image", "document"},
    "google": {"audio", "image", "document", "video"},
    "openrouter": {"image"},
}

# Clé d'API attendue par fournisseur : sert à savoir s'il est « connecté ».
CLES: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

TYPES_MEDIA = ("audio", "image", "video", "document")

LIBELLES = {
    "audio": "Notes vocales et fichiers audio",
    "image": "Photos et images",
    "video": "Vidéos",
    "document": "Documents PDF",
}

# Modèles par défaut, par capacité et par fournisseur.
MODELES_DEFAUT: dict[tuple[str, str], str] = {
    ("audio", "openai"): "gpt-transcribe",
    ("audio", "google"): "gemini-3.7-flash",
    ("image", "anthropic"): "claude-sonnet-5",
    ("image", "openai"): "gpt-5.6-terra",
    ("image", "google"): "gemini-3.7-flash",
    ("image", "openrouter"): "moonshotai/kimi-k3",
    ("document", "anthropic"): "claude-sonnet-5",
    ("document", "openai"): "gpt-5.6-terra",
    ("document", "google"): "gemini-3.7-flash",
    ("video", "google"): "gemini-3.7-flash",
}

# Plafonds. Meta autorise jusqu'à 100 Mo pour un document, bien au-delà de ce
# qu'accepte une requête de modèle : sans plafond, l'appel échoue de façon opaque.
TAILLE_MAX_MO = float(os.getenv("MEDIA_TAILLE_MAX_MO") or "16")
TAILLE_MAX = int(TAILLE_MAX_MO * 1024 * 1024)


class MediaIndisponible(RuntimeError):
    """Le média ne peut pas être traité — le motif part en escalade tel quel."""


# ═════════════════════════════════════════════════════════════════════════
# Routage : qui traite quoi
# ═════════════════════════════════════════════════════════════════════════


def fournisseur_connecte(fournisseur: str) -> bool:
    """Une clé d'API est-elle renseignée pour ce fournisseur ?"""
    return bool(os.getenv(CLES.get(fournisseur, ""), "").strip())


def possible(type_media: str, fournisseur: str) -> bool:
    """Ce fournisseur peut-il traiter ce type, et est-il connecté ?"""
    if fournisseur == "escalade":
        return True
    return type_media in CAPACITES.get(fournisseur, set()) and fournisseur_connecte(fournisseur)


def _routage_fichier() -> dict:
    try:
        return yaml.safe_load(FICHIER_ROUTAGE.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return {}


def _defaut_env(type_media: str) -> str:
    """
    Choix initial, avant toute configuration depuis la console.

    On respecte les variables d'environnement si elles existent, sinon on prend
    le premier fournisseur capable ET connecté. À défaut, escalade : mieux vaut
    un humain prévenu qu'un client ignoré.
    """
    explicite = os.getenv(f"MEDIA_{type_media.upper()}_FOURNISSEUR", "").strip().lower()
    if explicite:
        return explicite
    global_ = os.getenv("MEDIA_FOURNISSEUR", "").strip().lower()
    if global_:
        return global_

    principal = (os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()
    if possible(type_media, principal):
        return principal
    for candidat in ("openai", "google", "anthropic", "openrouter"):
        if possible(type_media, candidat):
            return candidat
    return "escalade"


def _entree(type_media: str) -> dict:
    """
    Ligne de routage d'un type, quel que soit le format enregistré.

    Les premières versions écrivaient une simple chaîne (« openai ») ; on gère
    les deux pour ne pas casser une configuration déjà en place.
    """
    brut = _routage_fichier().get(type_media)
    if isinstance(brut, str):
        return {"fournisseur": brut.strip().lower(), "modele": ""}
    if isinstance(brut, dict):
        return {
            "fournisseur": str(brut.get("fournisseur", "")).strip().lower(),
            "modele": str(brut.get("modele", "")).strip(),
        }
    return {"fournisseur": "", "modele": ""}


def fournisseur_pour(type_media: str) -> str:
    """Fournisseur retenu pour ce type : console d'abord, puis .env."""
    return _entree(type_media)["fournisseur"] or _defaut_env(type_media)


def modele_pour(type_media: str, fournisseur: str) -> str:
    """Modèle retenu : console, puis .env, puis le défaut du fournisseur."""
    choisi = _entree(type_media)["modele"]
    if choisi:
        return choisi
    explicite = os.getenv(f"MEDIA_{type_media.upper()}_MODELE", "").strip()
    if explicite:
        return explicite
    return MODELES_DEFAUT.get((type_media, fournisseur), "")


# ── Modèles disponibles chez un fournisseur ──────────────────────────────
#
# Interrogés en direct plutôt que codés en dur : les catalogues bougent tous les
# mois, et une liste figée dans le code proposerait des modèles retirés tout en
# masquant les nouveaux.

_URLS_MODELES = {
    "openai": ("https://api.openai.com/v1/models", "bearer"),
    "openrouter": ("https://openrouter.ai/api/v1/models", "bearer"),
    "google": ("https://generativelanguage.googleapis.com/v1beta/models", "google"),
    "anthropic": ("https://api.anthropic.com/v1/models", "anthropic"),
}

# Mots-clés servant à repérer les modèles pertinents pour chaque usage.
_INDICES = {
    "audio": ("transcribe", "whisper", "audio", "gemini"),
    "image": ("gpt", "claude", "gemini", "vision", "vl", "kimi", "llama", "qwen", "mistral"),
    "video": ("gemini",),
    "document": ("gpt", "claude", "gemini", "vision", "kimi", "llama", "qwen", "mistral"),
}


async def modeles_disponibles(fournisseur: str, type_media: str = "") -> list[str]:
    """
    Catalogue du fournisseur, filtré sur ce qui a du sens pour ce type.

    En cas d'échec (clé absente, réseau, API modifiée) on retombe sur le modèle
    par défaut plutôt que de laisser une liste vide : l'utilisateur doit toujours
    pouvoir choisir quelque chose.
    """
    entree = _URLS_MODELES.get(fournisseur)
    cle = os.getenv(CLES.get(fournisseur, ""), "").strip()
    secours = [m for (t, f), m in MODELES_DEFAUT.items() if f == fournisseur and (not type_media or t == type_media)]

    if not entree or not cle:
        return sorted(set(secours))

    url, mode = entree
    entetes: dict[str, str] = {"User-Agent": UA}
    params: dict[str, str] = {}
    if mode == "bearer":
        entetes["Authorization"] = f"Bearer {cle}"
    elif mode == "anthropic":
        entetes["x-api-key"] = cle
        entetes["anthropic-version"] = "2023-06-01"
        params["limit"] = "100"
    else:
        params["key"] = cle

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers=entetes, params=params)
        if r.status_code != 200:
            logger.info(f"Catalogue {fournisseur} indisponible ({r.status_code})")
            return sorted(set(secours))
        d = r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.info(f"Catalogue {fournisseur} injoignable : {e}")
        return sorted(set(secours))

    noms: list[str] = []
    for item in d.get("data") or d.get("models") or []:
        nom = item.get("id") or item.get("name") or ""
        # Google préfixe ses identifiants par « models/ »
        nom = nom.split("/", 1)[1] if nom.startswith("models/") else nom
        if nom:
            noms.append(nom)

    if type_media:
        indices = _INDICES.get(type_media, ())
        filtres = [n for n in noms if any(i in n.lower() for i in indices)]
        # Un filtre qui ne laisse rien serait pire que pas de filtre du tout.
        noms = filtres or noms

    return sorted(set(noms + secours))


def tableau_routage() -> dict:
    """
    État complet du routage, tel que l'affiche la console.

    Chaque ligne est un type de média, chaque colonne un fournisseur. Une case
    impossible — capacité absente ou clé non renseignée — est marquée pour être
    grisée dans l'interface, avec la raison. C'est plus honnête que de laisser
    choisir une option qui échouera au premier vocal reçu.
    """
    colonnes = []
    for f in CAPACITES:
        colonnes.append(
            {"id": f, "nom": f.capitalize(), "connecte": fournisseur_connecte(f),
             "cle": CLES[f]}
        )

    lignes = []
    for t in TYPES_MEDIA:
        cases = []
        for f in CAPACITES:
            capable = t in CAPACITES[f]
            connecte = fournisseur_connecte(f)
            if not capable:
                raison = f"{f.capitalize()} ne sait pas traiter ce type de fichier."
            elif not connecte:
                raison = f"Clé {CLES[f]} non renseignée."
            else:
                raison = ""
            cases.append({"fournisseur": f, "possible": capable and connecte, "raison": raison})
        lignes.append(
            {
                "type": t,
                "libelle": LIBELLES[t],
                "choix": fournisseur_pour(t),
                "modele": modele_pour(t, fournisseur_pour(t)),
                "cases": cases,
                "actif": actif(t),
            }
        )
    return {"colonnes": colonnes, "lignes": lignes}


def enregistrer_routage(choix: dict) -> dict:
    """
    Écrit le routage choisi depuis la console, en refusant l'impossible.

    Chaque entrée accepte soit une chaîne (le fournisseur seul), soit
    {"fournisseur": ..., "modele": ...}.
    """
    propre = {}
    for t, valeur in (choix or {}).items():
        if t not in TYPES_MEDIA:
            continue
        if isinstance(valeur, dict):
            f = str(valeur.get("fournisseur", "")).strip().lower()
            modele = str(valeur.get("modele", "")).strip()
        else:
            f, modele = str(valeur).strip().lower(), ""

        if f != "escalade" and not possible(t, f):
            raise MediaIndisponible(
                f"{f.capitalize()} ne peut pas traiter « {LIBELLES.get(t, t)} »."
            )
        propre[t] = {"fournisseur": f, "modele": "" if f == "escalade" else modele}

    FICHIER_ROUTAGE.parent.mkdir(exist_ok=True)
    FICHIER_ROUTAGE.write_text(
        yaml.dump(propre, allow_unicode=True, sort_keys=True), encoding="utf-8"
    )
    logger.info(f"Routage des médias modifié : {propre}")
    return propre


def actif(type_media: str) -> bool:
    """Ce type est-il traité, ou part-il directement en escalade ?"""
    return fournisseur_pour(type_media) != "escalade"


# ═════════════════════════════════════════════════════════════════════════
# Téléchargement chez Meta
# ═════════════════════════════════════════════════════════════════════════


async def _resoudre_url(media_id: str) -> tuple[str, str, int]:
    """Étape A : l'identifiant du média donne une URL fraîche."""
    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    version = os.getenv("META_API_VERSION") or "v25.0"
    if not token:
        raise MediaIndisponible("META_ACCESS_TOKEN manquant : téléchargement impossible.")

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"https://graph.facebook.com/{version}/{media_id}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": UA},
        )
    if r.status_code != 200:
        raise MediaIndisponible(f"Meta a refusé la résolution du média ({r.status_code}).")

    d = r.json()
    return d.get("url", ""), d.get("mime_type", ""), int(d.get("file_size") or 0)


async def _octets(url: str) -> bytes:
    """
    Étape B : télécharger.

    Le header Authorization est OBLIGATOIRE, même si l'URL ressemble à un lien
    CDN public. C'est l'erreur d'implémentation la plus fréquente sur cette API :
    sans lui, la requête échoue sans que rien n'indique pourquoi.
    """
    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        r = await client.get(
            url, headers={"Authorization": f"Bearer {token}", "User-Agent": UA}
        )
    r.raise_for_status()
    return r.content


async def telecharger(media_id: str, url_webhook: str = "") -> tuple[bytes, str]:
    """
    Récupère les octets d'un média.

    L'URL livrée par le webhook expire en 5 minutes. Comme l'agent répond 200
    puis travaille en tâche de fond, derrière un verrou par numéro, elle peut
    très bien être morte quand on y arrive — trois vocaux d'affilée suffisent.
    On l'essaie parce qu'elle évite un aller-retour, mais on sait re-résoudre.
    C'est pourquoi seul `media_id` est conservé, jamais l'URL.
    """
    if url_webhook:
        try:
            octets = await _octets(url_webhook)
            if octets:
                return octets, ""
        except (httpx.HTTPError, httpx.HTTPStatusError) as e:
            logger.info(f"URL du webhook périmée ou refusée ({e}) : re-résolution par media_id")

    url, mime, taille = await _resoudre_url(media_id)
    if taille and taille > TAILLE_MAX:
        raise MediaIndisponible(
            f"Fichier de {taille // (1024 * 1024)} Mo, au-delà du plafond de "
            f"{int(TAILLE_MAX_MO)} Mo."
        )
    if not url:
        raise MediaIndisponible("Meta n'a pas fourni d'URL pour ce média.")

    try:
        octets = await _octets(url)
    except (httpx.HTTPError, httpx.HTTPStatusError) as e:
        raise MediaIndisponible(f"Téléchargement impossible : {e}") from e

    if len(octets) > TAILLE_MAX:
        raise MediaIndisponible(
            f"Fichier de {len(octets) // (1024 * 1024)} Mo, au-delà du plafond de "
            f"{int(TAILLE_MAX_MO)} Mo."
        )
    return octets, mime


# ═════════════════════════════════════════════════════════════════════════
# Transcodage audio
# ═════════════════════════════════════════════════════════════════════════


def _ffmpeg_disponible() -> bool:
    return shutil.which("ffmpeg") is not None


def transcoder_audio(octets: bytes) -> tuple[bytes, str]:
    """
    Convertit l'audio WhatsApp en WAV 16 kHz mono.

    Les notes vocales arrivent en Ogg/Opus. Les documentations d'OpenAI et de
    Gemini listent « audio/ogg » sans jamais confirmer Opus — Gemini écrit même
    « OGG Vorbis ». Plutôt que de parier, on transcode : 40 ms de calcul qui
    suppriment toute une classe de pannes silencieuses.

    Sans ffmpeg (installation locale minimale), on envoie l'Ogg tel quel : ça
    fonctionne souvent, et l'échec éventuel part en escalade.
    """
    if not _ffmpeg_disponible():
        logger.info("ffmpeg absent : l'audio est envoyé au format d'origine")
        return octets, "audio/ogg"

    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
            input=octets, capture_output=True, timeout=60, check=True,
        )
        return r.stdout, "audio/wav"
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Transcodage audio impossible, envoi du format d'origine : {e}")
        return octets, "audio/ogg"


# ═════════════════════════════════════════════════════════════════════════
# Conversion en texte
# ═════════════════════════════════════════════════════════════════════════


def _client_openai(fournisseur: str):
    from openai import AsyncOpenAI

    from agent.llm import COMPATIBLES_OPENAI

    if fournisseur == "openai":
        base, variable = None, "OPENAI_API_KEY"
    else:
        base, variable, _ = COMPATIBLES_OPENAI[fournisseur]
    cle = os.getenv(variable, "").strip()
    if not cle:
        raise MediaIndisponible(f"{variable} est vide.")
    return AsyncOpenAI(api_key=cle, base_url=base)


async def _transcrire(octets: bytes, fournisseur: str, modele: str) -> str:
    """Audio → texte."""
    audio, mime = await asyncio.to_thread(transcoder_audio, octets)
    nom = "audio.wav" if mime == "audio/wav" else "audio.ogg"

    if fournisseur == "openai":
        client = _client_openai("openai")
        r = await client.audio.transcriptions.create(
            model=modele or "gpt-transcribe", file=(nom, audio, mime)
        )
        return (getattr(r, "text", "") or "").strip()

    if fournisseur == "google":
        # Gemini n'a pas d'endpoint de transcription : on le lui demande dans
        # un message ordinaire, l'audio passant en pièce jointe.
        client = _client_openai("google")
        r = await client.chat.completions.create(
            model=modele or "gemini-3.7-flash",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Transcris fidèlement cet audio, sans commentaire."},
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(audio).decode(),
                    "format": "wav" if mime == "audio/wav" else "ogg"}},
            ]}],
        )
        _compter(r, modele)
        return (r.choices[0].message.content or "").strip()

    raise MediaIndisponible(f"{fournisseur.capitalize()} ne sait pas transcrire l'audio.")


async def _vision(octets: bytes, mime: str, consigne: str, fournisseur: str, modele: str) -> str:
    """Image (ou PDF scanné) → description textuelle."""
    b64 = base64.b64encode(octets).decode()

    if fournisseur == "anthropic":
        from anthropic import AsyncAnthropic

        cle = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not cle:
            raise MediaIndisponible("ANTHROPIC_API_KEY est vide.")
        client = AsyncAnthropic(api_key=cle)
        # L'image AVANT le texte : Anthropic documente que l'ordre améliore le
        # résultat. Et jamais source "url" : Anthropic irait chercher le fichier
        # lui-même, or l'URL Meta exige notre token.
        bloc = ({"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
                if mime == "application/pdf" else
                {"type": "image",
                 "source": {"type": "base64", "media_type": mime or "image/jpeg", "data": b64}})
        r = await client.messages.create(
            model=modele or "claude-sonnet-5", max_tokens=1024,
            messages=[{"role": "user", "content": [bloc, {"type": "text", "text": consigne}]}],
        )
        _compter(r, modele)
        return "\n".join(
            b.text for b in r.content if getattr(b, "type", None) == "text"
        ).strip()

    client = _client_openai(fournisseur)
    r = await client.chat.completions.create(
        model=modele or "gpt-5.6-terra", max_tokens=1024,
        messages=[{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:{mime or 'image/jpeg'};base64,{b64}"}},
            {"type": "text", "text": consigne},
        ]}],
    )
    _compter(r, modele)
    return (r.choices[0].message.content or "").strip()


async def _video(octets: bytes, mime: str, consigne: str, modele: str) -> str:
    """
    Vidéo → description. Gemini uniquement, en API native.

    C'est le seul fournisseur qui ingère réellement une vidéo (une image par
    seconde, plus la piste audio en parallèle). Les 16 Mo maximum de WhatsApp
    passent toujours en ligne, sans avoir à utiliser la Files API.
    """
    cle = os.getenv("GOOGLE_API_KEY", "").strip()
    if not cle:
        raise MediaIndisponible("GOOGLE_API_KEY est vide : impossible de lire la vidéo.")

    modele = modele or "gemini-3.7-flash"
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent",
            headers={"x-goog-api-key": cle, "Content-Type": "application/json"},
            json={"contents": [{"parts": [
                {"inline_data": {"mime_type": mime or "video/mp4",
                                 "data": base64.b64encode(octets).decode()}},
                {"text": consigne},
            ]}]},
        )
    if r.status_code != 200:
        raise MediaIndisponible(f"Analyse vidéo refusée ({r.status_code}).")

    d = r.json()
    parts = (d.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return "\n".join(p.get("text", "") for p in parts).strip()


def _extraire_pdf(octets: bytes) -> str:
    """Texte d'un PDF natif. Vide si le document est un scan."""
    try:
        import io

        from pypdf import PdfReader

        lecteur = PdfReader(io.BytesIO(octets))
        pages = [(p.extract_text() or "") for p in lecteur.pages[:30]]
        return "\n".join(pages).strip()
    except Exception as e:  # noqa: BLE001
        logger.info(f"Extraction PDF impossible : {e}")
        return ""


def _compter(reponse, modele: str) -> None:
    """
    Impute la dépense au plafond quotidien.

    Sans ça, le coupe-circuit ne protège plus rien : quelqu'un qui envoie des
    vidéos en boucle contournerait entièrement PLAFOND_DEPENSE_JOUR.
    """
    try:
        u = getattr(reponse, "usage", None)
        if u is None:
            return
        entree = getattr(u, "input_tokens", None) or getattr(u, "prompt_tokens", 0) or 0
        sortie = getattr(u, "output_tokens", None) or getattr(u, "completion_tokens", 0) or 0
        depenses.enregistrer(modele or "claude-sonnet-5", int(entree), int(sortie))
    except Exception:  # noqa: BLE001
        pass


# ═════════════════════════════════════════════════════════════════════════
# Point d'entrée
# ═════════════════════════════════════════════════════════════════════════

CONSIGNE_IMAGE = (
    "Décris cette image pour un conseiller du service client qui ne la voit pas. "
    "Sois factuel et concis. Mentionne tout texte lisible, référence, marque ou "
    "numéro. N'invente rien."
)
CONSIGNE_PDF = (
    "Retranscris le contenu utile de ce document pour un conseiller du service "
    "client. Garde les montants, dates, références et noms exactement tels quels."
)
CONSIGNE_VIDEO = (
    "Décris ce que montre cette vidéo pour un conseiller du service client qui ne "
    "peut pas la regarder. Rapporte aussi ce qui est dit à voix haute. Sois factuel."
)

PREFIXES = {
    "audio": "note vocale transcrite",
    "image": "image reçue",
    "video": "vidéo reçue",
    "document": "document reçu",
}


async def convertir_en_texte(msg) -> str:
    """
    Transforme le média d'un message en texte exploitable par l'agent.

    Lève MediaIndisponible avec un motif lisible dès que le traitement n'est pas
    possible : l'appelant escalade alors vers un humain, plutôt que de laisser
    le client sans réponse.
    """
    type_media = msg.type_media
    if type_media not in TYPES_MEDIA:
        raise MediaIndisponible(
            "Type de fichier non pris en charge (autocollant, contact, position…)."
        )

    fournisseur = fournisseur_pour(type_media)
    if fournisseur == "escalade":
        raise MediaIndisponible(
            f"Le traitement automatique de « {LIBELLES[type_media]} » est désactivé."
        )
    if not possible(type_media, fournisseur):
        raise MediaIndisponible(
            f"{fournisseur.capitalize()} ne peut pas traiter « {LIBELLES[type_media]} » "
            f"(capacité absente ou clé {CLES.get(fournisseur, '?')} manquante)."
        )

    modele = modele_pour(type_media, fournisseur)
    octets, mime_serveur = await telecharger(msg.media_id, msg.media_url)
    mime = msg.mime_type or mime_serveur

    if type_media == "audio":
        texte = await _transcrire(octets, fournisseur, modele)
        prefixe = "note vocale transcrite" if msg.est_vocal else "fichier audio transcrit"

    elif type_media == "image":
        texte = await _vision(octets, mime, CONSIGNE_IMAGE, fournisseur, modele)
        prefixe = PREFIXES["image"]

    elif type_media == "video":
        if fournisseur != "google":
            raise MediaIndisponible(
                "Seul Google Gemini sait analyser une vidéo. Choisissez-le pour "
                "les vidéos, ou laissez-les en escalade."
            )
        texte = await _video(octets, mime, CONSIGNE_VIDEO, modele)
        prefixe = PREFIXES["video"]

    else:  # document
        if "pdf" not in (mime or "").lower():
            raise MediaIndisponible(
                f"Seuls les PDF sont lus automatiquement (reçu : {mime or 'inconnu'})."
            )
        texte = await asyncio.to_thread(_extraire_pdf, octets)
        if len(texte) < 20:
            # Aucun texte : c'est un scan. On bascule sur la vision, qui fait
            # l'OCR — le cas des ordonnances et devis photographiés.
            logger.info("PDF sans texte extractible : bascule en OCR par le modèle")
            texte = await _vision(octets, "application/pdf", CONSIGNE_PDF, fournisseur, modele)
        prefixe = PREFIXES["document"]
        if msg.nom_fichier:
            prefixe = f"{prefixe} « {msg.nom_fichier} »"

    if not texte:
        raise MediaIndisponible("Le fichier n'a produit aucun contenu exploitable.")

    morceaux = [f"[{prefixe}]", texte]
    if msg.legende:
        # La légende porte souvent l'intention réelle : « c'est ce modèle-là ».
        morceaux.append(f"[message accompagnant le fichier] {msg.legende}")
    return "\n".join(morceaux)
