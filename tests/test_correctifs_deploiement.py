"""
Non-régression des bugs rencontrés lors d'un déploiement réel.

Chacun de ces tests échoue sur la version qui contenait le bug : c'est leur
seule raison d'être. Ils sont regroupés ici plutôt que dispersés pour que le
lien avec l'incident reste lisible.
"""

import os
import re

import pytest


# ── .env.example vide et simulateur ──────────────────────────────────────


def test_une_variable_meta_vide_ne_casse_pas_le_simulateur(monkeypatch):
    """
    .env.example livre META_APP_SECRET= (vide). python-dotenv place la variable
    dans l'environnement avec une chaîne vide ; os.environ.setdefault la voyait
    « déjà définie » et s'abstenait. Le simulateur signait alors avec son secret
    pendant que la vérification attendait "" : 401 sur chaque message, dès
    l'étape 1, en ayant suivi la procédure à la lettre.
    """
    from agent.providers.simulateur import SECRET_SIMULATEUR

    monkeypatch.setenv("META_APP_SECRET", "")
    monkeypatch.setenv("META_ACCESS_TOKEN", "")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "")
    monkeypatch.setenv("WHATSAPP_PROVIDER", "simulateur")

    from agent.providers.simulateur import FournisseurSimulateur

    FournisseurSimulateur()
    assert os.environ["META_APP_SECRET"] == SECRET_SIMULATEUR
    assert os.environ["META_ACCESS_TOKEN"] == "simulateur"


def test_le_webhook_du_simulateur_se_verifie_avec_une_variable_vide(monkeypatch):
    """Bout en bout : la signature émise doit être celle qu'on vérifie."""
    monkeypatch.setenv("META_APP_SECRET", "")
    monkeypatch.setenv("WHATSAPP_PROVIDER", "simulateur")

    from agent.providers.simulateur import FournisseurSimulateur, payload_signe

    FournisseurSimulateur()
    corps, entetes = payload_signe("bonjour")

    import hashlib
    import hmac

    attendue = "sha256=" + hmac.new(
        os.environ["META_APP_SECRET"].encode(), corps, hashlib.sha256
    ).hexdigest()
    assert entetes["X-Hub-Signature-256"] == attendue


# ── Recherche documentaire ───────────────────────────────────────────────


def _knowledge(tmp_path, monkeypatch, contenu: str):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "doc.md").write_text(contenu, encoding="utf-8")
    import importlib

    import agent.tools as tools

    importlib.reload(tools)
    return tools


def test_un_terme_de_deux_caracteres_est_cherchable(tmp_path, monkeypatch):
    """
    L'ancien filtre « len(m) > 2 » éliminait les unités et les sigles courts —
    « kg », « cl », « m2 » — souvent centraux dans un catalogue. La recherche ne
    renvoyait rien, et le modèle comblait le vide en reprenant un montant vu
    ailleurs, c'est-à-dire en inventant un tarif.
    """
    tools = _knowledge(tmp_path, monkeypatch, "Pain de campagne 1 kg : 6,80 EUR.\n")
    assert "6,80" in tools.rechercher_information("kg")
    assert "6,80" in tools.rechercher_information("le pain de 1 kg")


def test_les_mots_vides_ne_noient_pas_les_resultats(tmp_path, monkeypatch):
    """
    Abaisser le seuil sans filtrer « de », « la », « et » ferait matcher presque
    chaque ligne : le plafond de 25 se remplirait de bruit.
    """
    lignes = "\n".join(f"Ligne {i} de la documentation et du reste." for i in range(40))
    tools = _knowledge(tmp_path, monkeypatch, lignes)
    assert "Aucune information" in tools.rechercher_information("de la et")


def test_la_ligne_la_plus_pertinente_arrive_en_premier(tmp_path, monkeypatch):
    """
    Sans classement, les 25 lignes sortaient dans l'ordre des fichiers : la
    réponse exacte pouvait rester hors du plafond.
    """
    contenu = "\n".join(
        ["Pain : generalites sur nos farines."] * 30
        + ["Pain sans gluten, commande la veille : 5,90 EUR."]
    )
    tools = _knowledge(tmp_path, monkeypatch, contenu)
    premiere = tools.rechercher_information("pain sans gluten").splitlines()[0]
    assert "5,90" in premiere


# ── Escalade et sécurité ─────────────────────────────────────────────────


def test_l_escalade_n_efface_pas_les_consignes_de_securite():
    """
    La valeur de retour de passer_la_main est lue par le modèle et dicte le
    dernier message reçu par le client. Formulée seulement comme « dis qu'un
    humain revient », elle produisait exactement cela — et supprimait les
    consignes de sécurité déjà rédigées, dans les cas où elles comptent.
    """
    import inspect

    import agent.tools as tools

    source = inspect.getsource(tools.passer_la_main)
    assert "sécurité" in source
    assert "JAMAIS supprimé" in source or "jamais supprimé" in source.lower()
    assert "récapitulatif" in source


def test_la_borne_de_delai_est_annoncee_comme_technique(tmp_path, monkeypatch):
    """
    verifier_delai renvoie un horodatage exact. Cité tel quel, il donnait
    « à partir de 16h02 » au client, ce qui ne veut rien dire pour lui.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "entreprise.yaml").write_text(
        "delais_heures:\n  ceremonie: 72\n", encoding="utf-8"
    )
    import importlib

    import agent.tools as tools

    importlib.reload(tools)
    r = tools.verifier_delai("ceremonie", "2020-01-01 10:00")
    assert "REFUSÉ" in r
    assert "BORNE TECHNIQUE" in r


# ── Fichiers livrés ──────────────────────────────────────────────────────


def test_le_prompt_d_exemple_ne_cite_que_des_outils_existants():
    """
    prompts.exemple.yaml mentionnait transferer_a_humain, qui n'existe pas :
    l'outil s'appelle passer_la_main. Toute configuration dérivée de l'exemple
    héritait d'un nom d'outil fantôme.
    """
    import pathlib

    import yaml

    from agent.tools import schemas_outils

    racine = pathlib.Path(__file__).resolve().parent.parent
    exemple = yaml.safe_load(
        (racine / "config" / "prompts.exemple.yaml").read_text(encoding="utf-8")
    )
    reels = {o["name"] for o in schemas_outils()}
    cites = set(re.findall(r"\b([a-z_]+_[a-z_]+)\b", exemple["system_prompt"]))
    inventes = {c for c in cites if c.endswith(("_information", "_delai", "_demande", "_humain", "_main"))} - reels
    assert not inventes, f"outils inexistants cités dans l'exemple : {inventes}"


def test_env_example_ne_declare_pas_deux_fois_la_meme_variable():
    """
    ADMIN_TOKEN y figurait deux fois, avec des commentaires différents : la
    seconde occurrence l'emportait silencieusement.
    """
    import pathlib
    from collections import Counter

    racine = pathlib.Path(__file__).resolve().parent.parent
    lignes = (racine / ".env.example").read_text(encoding="utf-8").splitlines()
    cles = [l.split("=", 1)[0] for l in lignes if l and not l.startswith("#") and "=" in l]
    doublons = [c for c, n in Counter(cles).items() if n > 1]
    assert not doublons, f"variables déclarées plusieurs fois : {doublons}"


def test_le_compose_passe_les_variables_au_conteneur():
    """
    docker compose ne transmet pas l'environnement de l'hôte au conteneur.
    Sans bloc environment et sans .env, l'agent démarrait « healthy » en mode
    simulateur, sans clé ni identifiants — sans que rien ne le signale.
    """
    import pathlib

    import yaml

    racine = pathlib.Path(__file__).resolve().parent.parent
    compose = yaml.safe_load((racine / "docker-compose.yaml").read_text(encoding="utf-8"))
    env = compose["services"]["agent"]["environment"]

    for cle in ("WHATSAPP_PROVIDER", "OPENAI_API_KEY", "META_APP_SECRET", "ADMIN_TOKEN"):
        assert cle in env, f"{cle} n'est pas transmise au conteneur"

    # Les variables sans lesquelles rien ne peut fonctionner doivent faire
    # échouer le déploiement, pas le laisser démarrer à moitié.
    for cle in ("WHATSAPP_PROVIDER", "SESSION_SECRET"):
        assert ":?" in str(env[cle]), f"{cle} devrait être obligatoire (:?)"
    assert ":?" in str(env["DATABASE_URL"])


def test_le_env_file_du_compose_est_optionnel():
    """Un clone frais n'a pas de .env : il est gitignoré."""
    import pathlib

    import yaml

    racine = pathlib.Path(__file__).resolve().parent.parent
    compose = yaml.safe_load((racine / "docker-compose.yaml").read_text(encoding="utf-8"))
    env_file = compose["services"]["agent"]["env_file"]
    assert isinstance(env_file, list)
    assert env_file[0]["required"] is False


# ── Modèles OpenAI à raisonnement ────────────────────────────────────────


class _FauxMessage:
    content = "bonjour"
    tool_calls = None

    def model_dump(self, **_):
        return {"role": "assistant", "content": self.content}


class _FauxChoix:
    finish_reason = "stop"
    message = _FauxMessage()


class _FausseReponse:
    usage = None
    choices = [_FauxChoix()]


class _FauxClientOpenAI:
    """Reproduit le refus d'OpenAI : outils + reasoning_effort = 400."""

    def __init__(self, refuse_effort: bool):
        self.refuse_effort = refuse_effort
        self.appels: list[dict] = []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.appels.append(kwargs)
        if self.refuse_effort and "reasoning_effort" in kwargs:
            raise RuntimeError(
                "Error code: 400 - Function tools with reasoning_effort are not "
                "supported for gpt-5.6-terra in /v1/chat/completions."
            )
        return _FausseReponse()


def _client(refuse_effort: bool):
    from agent.llm import ClientCompatibleOpenAI

    c = ClientCompatibleOpenAI.__new__(ClientCompatibleOpenAI)
    c.nom = "openai"
    c.modele = "gpt-5.6-terra"
    c.max_tokens = 100
    c._supporte_effort_none = True
    c.client = _FauxClientOpenAI(refuse_effort)
    return c


OUTIL = [{"name": "x", "description": "x", "input_schema": {"type": "object", "properties": {}}}]


@pytest.mark.asyncio
async def test_openai_envoie_reasoning_effort_none():
    """
    Les gpt-5.6-* appliquent un reasoning_effort par défaut qu'OpenAI refuse
    dès qu'on joint des outils. L'agent en utilise à chaque tour : sans
    reasoning_effort="none", CHAQUE message part en erreur 400 et le client
    reçoit « je rencontre un problème technique ».
    """
    c = _client(refuse_effort=False)
    await c.converser("sys", [], "salut", OUTIL, None)
    assert c.client.appels[0].get("reasoning_effort") == "none"


@pytest.mark.asyncio
async def test_openai_retombe_sans_effort_si_le_modele_refuse():
    """Un modèle qui ignore le paramètre ne doit pas rester bloqué."""
    c = _client(refuse_effort=True)
    bilan = await c.converser("sys", [], "salut", OUTIL, None)
    assert bilan.texte == "bonjour"
    assert "reasoning_effort" in c.client.appels[0]
    assert "reasoning_effort" not in c.client.appels[1]
    assert c._supporte_effort_none is False


def test_toute_variable_lue_par_le_code_est_transmise_au_conteneur():
    """
    Une variable réglée chez l'hébergeur mais absente du bloc environment est
    ignorée en silence : docker compose ne transmet pas l'environnement de
    l'hôte au conteneur. C'est ainsi qu'ANTHROPIC_MAX_TOKENS, pourtant
    documentée dans .env.example, restait sans effet une fois déployée.

    Ce test rattrape l'oubli à chaque ajout de variable, ce qu'une relecture
    humaine ne fait pas de façon fiable.
    """
    import pathlib

    import yaml

    racine = pathlib.Path(__file__).resolve().parent.parent

    lues: set[str] = set()
    for f in (racine / "agent").glob("**/*.py"):
        t = f.read_text(encoding="utf-8")
        lues |= set(re.findall(r'os\.(?:getenv|environ\.get)\(\s*["\']([A-Z_0-9]+)["\']', t))
        lues |= set(re.findall(r'os\.environ\[\s*["\']([A-Z_0-9]+)["\']', t))

    compose = yaml.safe_load((racine / "docker-compose.yaml").read_text(encoding="utf-8"))
    passees = set(compose["services"]["agent"]["environment"])

    # Posées par l'hébergeur ou construites par le compose lui-même.
    hebergeur = {
        "RAILWAY_ENVIRONMENT", "COOLIFY_URL", "RENDER", "FLY_APP_NAME", "PORT",
        "DYNO", "KUBERNETES_SERVICE_HOST", "DATABASE_URL", "SOURCE_COMMIT",
        "COOLIFY_CONTAINER_NAME", "RAILWAY_PROJECT_ID", "RENDER_SERVICE_ID",
    }

    # Dépannage ponctuel, jamais de la configuration : elle se passe le temps
    # d'une commande, à la main. L'inscrire dans le compose reviendrait à
    # proposer de stocker un mot de passe en clair chez l'hébergeur.
    depannage = {"AGENTKIT_NOUVEAU_MDP"}

    oubliees = sorted(lues - passees - hebergeur - depannage)
    assert not oubliees, (
        "variables lues par le code mais non transmises au conteneur "
        f"(elles seraient ignorées en silence une fois déployées) : {oubliees}"
    )


# ── Exemples livrés ──────────────────────────────────────────────────────


def _racine():
    import pathlib

    return pathlib.Path(__file__).resolve().parent.parent


def test_l_exemple_plomberie_est_complet_et_valide():
    """Un exemple cassé se recopie tel quel chez un client."""
    import yaml

    base = _racine() / "exemples" / "plomberie"
    entreprise = yaml.safe_load((base / "entreprise.yaml").read_text(encoding="utf-8"))
    prompts = yaml.safe_load((base / "prompts.yaml").read_text(encoding="utf-8"))

    assert entreprise["delais_heures"], "delais_heures est le seul bloc lu par le code"
    for cle in ("system_prompt", "fallback_message", "error_message", "quota_message"):
        assert prompts.get(cle), f"{cle} manquant dans l'exemple"

    docs = list((base / "knowledge").glob("*.md"))
    assert len(docs) >= 5, "l'exemple complet doit montrer plusieurs documents"


def test_les_exemples_ne_citent_que_des_outils_existants():
    """Même garde que pour prompts.exemple.yaml, appliquée au second exemple."""
    import yaml

    from agent.tools import schemas_outils

    reels = {o["name"] for o in schemas_outils()}
    texte = (_racine() / "exemples" / "plomberie" / "prompts.yaml").read_text(encoding="utf-8")
    cites = set(re.findall(r"\b([a-z]+_[a-z_]+)\b", texte))
    suspects = {c for c in cites if c.endswith(("_information", "_delai", "_demande", "_humain", "_main"))}
    assert not (suspects - reels), f"outils inexistants : {suspects - reels}"


# Numéros publics d'urgence : ils DOIVENT rester exacts. Les fausser dans un
# exemple de consigne de sécurité serait dangereux pour qui recopie le fichier.
NUMEROS_PUBLICS = {"0 800 47 33 33"}

# Plages françaises non attribuées, réservées à la documentation.
PREFIXES_FICTIFS = ("01 99", "02 99", "03 99", "04 99", "05 99")


def test_les_exemples_n_exposent_aucune_coordonnee_reelle():
    """
    Un dépôt public ne diffuse pas de téléphone ni d'adresse joignables : un
    exemple se recopie, et l'entreprise citée recevrait de vrais appels.

    Détection par motif plutôt que par liste noire — une liste noire ne protège
    que des cas déjà connus, et fait figurer dans le dépôt les chaînes mêmes
    qu'elle prétend en écarter.
    """
    base = _racine() / "exemples"
    for f in list(base.rglob("*.md")) + list(base.rglob("*.yaml")):
        t = f.read_text(encoding="utf-8")

        for numero in re.findall(r"\b0\d(?:[ .]\d{2}){4}\b", t):
            if numero in NUMEROS_PUBLICS:
                continue
            assert numero.startswith(PREFIXES_FICTIFS), (
                f"{f.name} expose un numéro potentiellement réel : {numero}. "
                "Utilisez une plage fictive (01 99 ...) ou ajoutez-le aux "
                "numéros publics s'il s'agit d'un service d'urgence."
            )

        for adresse in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", t):
            assert adresse.endswith(("@exemple.fr", "@exemple.com", "example.com")), (
                f"{f.name} expose une adresse potentiellement réelle : {adresse}"
            )


def test_les_faits_de_l_exemple_sont_trouvables(tmp_path, monkeypatch):
    """
    L'exemple sert de démonstration de rédaction : ses propres documents doivent
    être trouvables par la recherche du kit, sinon il enseigne le contraire de
    ce qu'il prêche.
    """
    import importlib
    import shutil

    source = _racine() / "exemples" / "plomberie" / "knowledge"
    monkeypatch.chdir(tmp_path)
    shutil.copytree(source, tmp_path / "knowledge")

    import agent.tools as tools

    importlib.reload(tools)

    for requete, attendu in (
        ("deboucher WC", "149"),
        ("entretien chaudiere", "159"),
        ("Bagnolet", "nous intervenons"),
        ("odeur de gaz", "0 800 47 33 33"),
        ("garantie depannage", "1 an"),
        ("qui soccupe des salles de bain", "Sofia"),
        ("intervenez vous a Meaux", "n'intervenons pas"),
    ):
        resultat = tools.rechercher_information(requete)
        assert attendu.lower() in resultat.lower(), (
            f"'{requete}' ne trouve pas '{attendu}' dans l'exemple livré"
        )

    # Sur une question tarifaire, la bonne ligne doit en plus arriver EN TÊTE :
    # c'est elle que le modèle recopie, et une ligne voisine le ferait annoncer
    # un prix qui n'est pas celui demandé.
    for requete, attendu in (("deboucher WC", "149"), ("entretien chaudiere", "159")):
        premiere = tools.rechercher_information(requete).splitlines()[0]
        assert attendu in premiere, f"'{requete}' : mauvaise ligne en tête — {premiere}"
