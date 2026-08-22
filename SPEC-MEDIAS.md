# Spécification — réception des médias

> État : **implémenté** (217 tests verts). Ce document reste la référence de
> conception : il explique les décisions et les pièges, pas seulement le code.
>
> Évolution depuis la version validée : le routage n'est plus une variable
> d'environnement unique mais **un service par type de fichier**, réglable
> depuis la console (onglet « Fichiers reçus »). Les variables ne servent plus
> que de valeur initiale.

---

## 1. Le problème

`agent/providers/meta.py` ignore aujourd'hui tout message dont `type != "text"` :

```python
if msg.get("type") != "text":
    logger.info(f"Message de type '{msg.get('type')}' ignoré")
    continue
```

Le client envoie une note vocale, l'agent ne répond **rien**. Pas une erreur, pas
une excuse : le silence. Sur WhatsApp, où le vocal est un usage massif, c'est le
défaut le plus visible du kit — et il ressemble à une panne.

**Objectif** : plus aucun message client ne reste sans réponse, quel que soit son
type.

---

## 2. La contrainte qui structure tout : Claude ne lit pas l'audio

L'API Messages d'Anthropic accepte texte, images et PDF. **Pas l'audio.** Ce
n'est pas une limite de notre code, c'est l'API. Or `anthropic` est le
fournisseur par défaut du kit.

Conséquence directe : **traiter l'audio impose une clé chez un second
fournisseur**. On ne peut pas le masquer, seulement le rendre clair et
paramétrable.

### Ce que chaque fournisseur sait faire

| | Audio | Image | PDF | Vidéo |
|---|:-:|:-:|:-:|:-:|
| **Anthropic** (défaut) | ❌ | ✅ natif | ✅ natif (≤32 Mo) | ❌ |
| **Google Gemini** | ✅ | ✅ | ✅ | ✅ **natif** |
| **OpenAI** | ✅ (`gpt-transcribe`) | ✅ | ✅ | ⚠️ frames uniquement |
| **OpenRouter** | selon le modèle | ✅ | selon | ⚠️ |

**Le chemin le plus simple pour l'intégrateur : `LLM_PROVIDER=google`.** Une
seule clé couvre les quatre médias, vidéo comprise, au tarif le plus bas. C'est
ce que la documentation recommandera à qui veut les médias sans réfléchir.

Qui tient à Claude pour la qualité de rédaction garde Claude, et ajoute une
seule clé de transcription. D'où la variable `MEDIA_FOURNISSEUR`, indépendante
de `LLM_PROVIDER`.

---

## 3. Architecture : média → texte → pipeline existant

Le média est converti en **texte** en amont, puis le flux actuel continue sans
changer.

```
webhook média
   ↓
téléchargement (2 étapes, chez Meta)
   ↓
conversion en texte  ← transcription | description | extraction | analyse
   ↓
MessageEntrant.texte  ← "[note vocale] Bonjour, je voudrais commander…"
   ↓
brain.py → mémoire → réponse          (INCHANGÉ)
```

Trois raisons de préférer ça à des blocs multimodaux natifs :

1. **Un seul chemin pour quatre fournisseurs.** Pas de code par modèle.
2. **L'humain lit le texte dans la console.** Il comprend une conversation sans
   réécouter un vocal — c'est ce qui rend l'escalade utilisable.
3. **`brain.py` et `llm.py` ne bougent pas.** Zéro risque sur ce qui fonctionne
   déjà et couvre 193 tests.

Le coût de ce choix, assumé : sur une image, on perd la finesse d'un modèle qui
« voit » l'image en même temps que la question. On envoie une description. Pour
un agent de service client — « c'est quoi ce produit ? », « ma pièce est cassée
ici » — c'est suffisant, et le gain de simplicité est décisif.

---

## 4. Téléchargement chez Meta — trois pièges vérifiés

### Le flux

1. Le webhook fournit désormais `url` directement dans l'objet média, **en plus**
   de `id`.
2. Si cette URL échoue : `GET /{version}/{MEDIA_ID}` → renvoie une `url` fraîche.
3. `GET <url>` → les octets.

### Piège n°1 — l'URL exige le token

`lookaside.fbsbx.com` ressemble à un CDN public. **Il ne l'est pas.** Sans
`Authorization: Bearer`, la requête échoue. C'est l'erreur d'implémentation la
plus fréquente sur cette API.

### Piège n°2 — l'URL expire en 5 minutes

Notre architecture répond `200` puis travaille en tâche de fond, avec un verrou
par numéro. Si un client envoie trois vocaux d'affilée, le troisième peut
attendre — et l'URL est morte.

**Décision** : on ne stocke **jamais** l'URL, seulement le `media_id`. La
fonction de téléchargement tente l'URL du webhook, et sur un 4xx **re-résout
l'ID**. Le cas est explicitement testé.

### Piège n°3 — le User-Agent

`lookaside.fbsbx.com` rejette certaines requêtes sans `User-Agent` explicite.
On en envoie un.

### Limites Meta en entrée

| Type | Taille max | Format reçu |
|---|---|---|
| Image | 5 Mo | JPEG, PNG |
| Audio | 16 Mo | **Ogg/Opus mono** (`audio/ogg; codecs=opus`) |
| Vidéo | 16 Mo | MP4 (H.264/AAC), 3GPP |
| Document | 100 Mo | PDF, DOCX, XLSX… |

Le document à 100 Mo dépasse la limite de requête de Claude (32 Mo) : un
**plafond configurable** s'impose, sinon l'appel échoue de façon opaque.

---

## 5. Traitement, par type

### Audio — transcription

Les notes vocales arrivent en **Ogg/Opus**. Les docs OpenAI et Gemini listent
`audio/ogg` sans confirmer explicitement Opus (Gemini écrit même « OGG Vorbis »).

**Décision** : si `ffmpeg` est présent, transcoder en WAV 16 kHz mono avant
l'envoi — 40 ms de CPU qui suppriment toute une classe de bugs. S'il est absent,
envoyer l'Ogg tel quel et escalader en cas d'échec. `ffmpeg` sera **ajouté au
Dockerfile** (donc présent en production) et resté optionnel en local.

Le champ `voice: true` distingue une note vocale d'un fichier audio joint : on
l'utilise pour le préfixe affiché (« note vocale » vs « fichier audio »).

### Image — description

Téléchargement → base64 → modèle de vision. **Jamais l'URL** : Anthropic irait
chercher l'image lui-même et n'a pas le token Meta.

La `caption` éventuelle est jointe : elle porte souvent l'intention réelle
(« c'est ce modèle que je veux »).

### Document PDF — extraction, puis OCR si besoin

1. Extraction du texte (`pypdf`).
2. Si le résultat est vide ou quasi vide → le PDF est un **scan**. Bascule sur le
   modèle de vision, qui fait l'OCR. C'est le cas que vous avez demandé
   explicitement, et il est fréquent : ordonnances, devis photographiés.
3. Au-delà du plafond de taille → escalade.

Les formats non-PDF (DOCX, XLSX) sont **hors périmètre** : escalade directe.

### Vidéo — analyse native, sinon escalade

Gemini ingère la vidéo nativement (1 frame/s + piste audio en parallèle), inline
jusqu'à 20 Mo — les 16 Mo de WhatsApp passent toujours. C'est la seule option qui
« comprend » réellement une vidéo, et la plus simple.

Si le fournisseur média configuré ne sait pas faire de vidéo → **escalade**. Pas
d'extraction de frames par ffmpeg dans cette première version : ça doublerait la
complexité pour un usage rare.

---

## 6. Le filet : tout échec escalade

Conformément à la décision prise : **aucun repli silencieux**. Chaque cas non
traité crée une escalade dans la console, avec le motif exact.

| Situation | Comportement |
|---|---|
| Type désactivé dans `MEDIAS_ACTIFS` | Escalade — « note vocale reçue, traitement désactivé » |
| Fournisseur incapable (audio + Anthropic seul) | Escalade — motif explicite, remède dans le message |
| Média trop lourd | Escalade — taille indiquée |
| Téléchargement ou traitement en échec | Escalade |
| Format non géré (DOCX, sticker…) | Escalade |

L'escalade porte `identifiant`, `motif`, et la `caption` si elle existe. L'humain
voit la conversation, sait qu'un média est arrivé, et répond.

**Le message du client est toujours enregistré**, même en cas d'échec — sinon la
trace disparaît précisément quand un humain doit reprendre la main.

---

## 7. Configuration — un service par type de fichier

Le routage se règle **depuis la console**, onglet « Fichiers reçus » : un tableau
dont les lignes sont les types de fichiers et les colonnes les services, plus une
colonne « Escalade ». Une case impossible — service incapable, ou clé absente —
est **grisée avec sa raison au survol**. Mieux vaut une case grise qu'une option
qui échouera au premier vocal reçu.

Le choix est écrit dans `config/medias.yaml`. Les variables d'environnement ne
servent que de valeur initiale :

```bash
# Vide = premier service capable ET connecté, sinon escalade.
MEDIA_AUDIO_FOURNISSEUR=openai
MEDIA_IMAGE_FOURNISSEUR=anthropic
MEDIA_VIDEO_FOURNISSEUR=google
MEDIA_DOCUMENT_FOURNISSEUR=anthropic
MEDIA_TAILLE_MAX_MO=16
```

Le repli automatique est conçu pour ne jamais surprendre : avec Claude en moteur
principal, l'audio bascule tout seul sur OpenAI s'il est connecté — et à défaut
tombe en escalade, jamais dans le silence.

La **revue de configuration** (`agent/environnement.py`) signalera le cas
incohérent — audio actif, fournisseur Anthropic, aucune clé média — au démarrage
**et** dans la console, avec le remède. C'est le même mécanisme qui a rattrapé
le simulateur laissé actif en production.

La dépense de transcription et de vision **compte dans `PLAFOND_DEPENSE_JOUR`**.
Sans ça, le coupe-circuit ne protège plus rien : quelqu'un qui envoie des vidéos
en boucle contourne le plafond.

### Coûts réels (ordre de grandeur)

| Média | Fournisseur | Coût |
|---|---|---|
| Vocal de 30 s | Gemini Flash | ~0,0004 $ |
| Vocal de 30 s | OpenAI `gpt-transcribe` | ~0,002 $ |
| Image | Claude Sonnet 5 | ~0,007 $ |
| Vidéo de 30 s | Gemini Flash | ~0,003 $ |

Négligeable à l'unité. C'est le volume qui compte, d'où le plafond.

---

## 8. Plan d'implémentation

Dans cet ordre, chaque étape testable seule.

| # | Fichier | Contenu |
|---|---|---|
| 1 | `agent/providers/base.py` | Champs média dans `MessageEntrant` : `type_media`, `media_id`, `mime_type`, `media_url`, `caption`, `est_vocal` |
| 2 | `agent/providers/meta.py` | Dispatch par type au lieu du filtre ; extraction des champs média |
| 3 | `agent/medias.py` *(nouveau)* | Téléchargement 2 étapes + re-résolution ; transcodage ffmpeg optionnel |
| 4 | `agent/medias.py` | Conversion en texte : transcription, vision, PDF+OCR, vidéo |
| 5 | `agent/main.py` | Branchement dans `traiter_message` : conversion avant `generer_reponse`, escalade sinon |
| 6 | `agent/environnement.py` | Alerte de configuration incohérente |
| 7 | `Dockerfile`, `requirements.txt` | `ffmpeg`, `pypdf` |
| 8 | `.env.example`, `README.md`, `AGENTS.md` | Documentation et matrice de compatibilité |

Rien de tout cela ne touche `brain.py`, `llm.py`, `memory.py` ni `admin.py`.

---

## 9. Tests end-to-end

Les tests existants frappent les routes HTTP réelles, pas les fonctions. Les
nouveaux suivent la même règle : **un webhook média signé entre, une réponse
sort**. Seuls les appels réseau externes (Meta, fournisseur de modèle) sont
simulés.

### Bout en bout, par type

| Test | Vérifie |
|---|---|
| Webhook audio → réponse envoyée | La transcription arrive bien dans le prompt |
| Webhook image + caption → réponse | La légende est jointe à la description |
| Webhook PDF texte → réponse | Extraction directe, sans appel vision |
| Webhook PDF scanné → réponse | Bascule OCR déclenchée quand le texte est vide |
| Webhook vidéo → réponse | Analyse native |

### Téléchargement

| Test | Vérifie |
|---|---|
| L'en-tête `Authorization: Bearer` est envoyé sur `lookaside` | Le piège n°1 |
| Un `User-Agent` est envoyé | Le piège n°3 |
| URL du webhook en 401 → re-résolution par `media_id` → succès | Le piège n°2 |
| Média au-delà du plafond → aucun téléchargement | La bande passante n'est pas gaspillée |

### Escalade — le filet

| Test | Vérifie |
|---|---|
| Type désactivé → escalade créée, client non ignoré | Le comportement choisi |
| Anthropic seul + audio → escalade au motif explicite | La contrainte du §2 |
| Transcription en échec → escalade, message client conservé | La trace subsiste |
| Sticker / DOCX → escalade | Formats hors périmètre |

### Non-régression

| Test | Vérifie |
|---|---|
| Un message texte reste traité exactement comme avant | Aucune régression |
| Déduplication sur un webhook média rejoué | Le réessai Meta ne double pas |
| La dépense média compte dans le plafond quotidien | Le coupe-circuit tient |

Cible : **~25 tests supplémentaires**, suite totale ~218, toujours verte.

---

## 10. Hors périmètre, volontairement

- **Envoi** de médias par l'agent (il répond en texte).
- **Formats bureautiques** (DOCX, XLSX) : escalade.
- **Extraction de frames vidéo** par ffmpeg pour les fournisseurs sans vidéo native.
- **Stockage des médias** : ils sont traités en mémoire puis jetés. Rien n'est
  écrit sur disque — moins de surface RGPD, pas de purge à gérer.
- **Transcription auto-hébergée** (faster-whisper). Le code passant par une API
  compatible OpenAI, il suffira plus tard de pointer `base_url` vers un Whisper
  local pour que l'audio ne quitte plus l'infrastructure. Prévu, pas fait.
