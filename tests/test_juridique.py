"""Documents juridiques publiés par l'agent."""

import re
import shutil
from pathlib import Path

import pytest
import yaml

RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture()
def conf(tmp_path, monkeypatch):
    """Une configuration juridique valide, dans un dossier de travail isolé."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(
        RACINE / "config" / "juridique.exemple.yaml",
        tmp_path / "config" / "juridique.yaml",
    )
    monkeypatch.setenv("RETENTION_JOURS", "90")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("MODE_TRANSPARENCE", "discrete")
    monkeypatch.setenv("LOG_MESSAGE_CONTENT", "false")
    # agent.main appelle load_dotenv() à l'import : le .env du poste de
    # développement entre alors dans os.environ et y reste. Sans ce nettoyage,
    # un MEDIA_AUDIO_FOURNISSEUR local ferait apparaître un sous-traitant que
    # le test n'a pas demandé — et le résultat dépendrait de la machine.
    for var in ("MEDIA_FOURNISSEUR", "MEDIA_AUDIO_FOURNISSEUR",
                "MEDIA_IMAGE_FOURNISSEUR", "MEDIA_VIDEO_FOURNISSEUR",
                "MEDIA_DOCUMENT_FOURNISSEUR"):
        monkeypatch.delenv(var, raising=False)
    import importlib

    import agent.juridique as J

    importlib.reload(J)
    return J


def test_les_documents_se_generent_sans_variable_orpheline(conf):
    """Un « {e['nom']} » resté dans le texte se voit tout de suite en public."""
    c = conf.contexte(conf.charger())
    for cle, (_, generer) in conf.documents_disponibles(c).items():
        texte = generer(c)
        assert not re.search(r"\{[a-z_]", texte), f"{cle} : variable non substituée"
        assert len(texte) > 800, f"{cle} : suspicieusement court"


def test_la_configuration_du_kit_prime_sur_le_fichier(conf, monkeypatch):
    """
    Une politique qui annonce 90 jours quand RETENTION_JOURS en vaut 30 est pire
    qu'une absence de politique : elle documente le manquement. La durée réelle
    doit donc venir de l'environnement, jamais d'une valeur recopiée à la main.
    """
    monkeypatch.setenv("RETENTION_JOURS", "30")
    c = conf.contexte(conf.charger())
    texte = conf.politique_confidentialite(c)
    assert "30 jours" in texte
    assert "90 jours" not in texte


def test_les_sous_traitants_suivent_le_fournisseur_choisi(conf, monkeypatch):
    """Nommer les destinataires est une obligation (art. 13 RGPD)."""
    monkeypatch.setenv("LLM_PROVIDER", "google")
    c = conf.contexte(conf.charger())
    noms = [t[0] for t in c["sous_traitants"]]
    assert any("Google" in n for n in noms)
    assert not any("OpenAI" in n for n in noms)
    assert any("Meta" in n for n in noms), "Meta achemine les messages, toujours"


def test_un_service_media_distinct_est_cite(conf, monkeypatch):
    """Une deuxième clé pour les vocaux ajoute un destinataire à déclarer."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MEDIA_AUDIO_FOURNISSEUR", "openai")
    c = conf.contexte(conf.charger())
    noms = [t[0] for t in c["sous_traitants"]]
    assert any("Anthropic" in n for n in noms)
    assert any("OpenAI" in n for n in noms)


def test_le_bandeau_reste_tant_que_la_revue_n_est_pas_faite(conf):
    """
    C'est le garde-fou du dispositif : sans lui, un brouillon généré en deux
    minutes reste en production pendant trois ans.
    """
    c = conf.contexte(conf.charger())
    assert c["revue_faite"] is False
    for _, generer in conf.documents_disponibles(c).values():
        assert "non relu par un professionnel du droit" in generer(c)


def test_le_bandeau_disparait_une_fois_la_revue_declaree(conf):
    donnees = yaml.safe_load(Path("config/juridique.yaml").read_text(encoding="utf-8"))
    donnees["revue_juridique"] = {
        "effectuee": True, "par": "Cabinet Exemple", "date": "2026-08-24",
    }
    Path("config/juridique.yaml").write_text(yaml.safe_dump(donnees, allow_unicode=True), encoding="utf-8")
    c = conf.contexte(conf.charger())
    texte = conf.politique_confidentialite(c)
    assert "non relu" not in texte
    assert "Cabinet Exemple" in texte


def test_l_annexe_de_sous_traitance_n_existe_qu_en_mode_agence(conf):
    c = conf.contexte(conf.charger())
    assert "sous-traitance" not in conf.documents_disponibles(c)

    donnees = yaml.safe_load(Path("config/juridique.yaml").read_text(encoding="utf-8"))
    donnees["mode"] = "agence"
    donnees["integrateur"] = {"raison_sociale": "Studio Exemple SAS", "email": "x@exemple.fr"}
    Path("config/juridique.yaml").write_text(yaml.safe_dump(donnees, allow_unicode=True), encoding="utf-8")

    c = conf.contexte(conf.charger())
    dispo = conf.documents_disponibles(c)
    assert "sous-traitance" in dispo
    assert "Studio Exemple SAS" in dispo["sous-traitance"][1](c)


def test_la_politique_explique_comment_demander_la_suppression(conf):
    """
    Exigence explicite de Meta : « all apps must inform users in their privacy
    policy how to request deletion of their data ».
    """
    c = conf.contexte(conf.charger())
    texte = conf.politique_confidentialite(c)
    assert "/legal/suppression" in texte
    assert c["protection_donnees"]["email"] in texte


def test_le_rendu_html_ne_laisse_pas_de_markdown(conf):
    c = conf.contexte(conf.charger())
    html = conf.page_html("T", conf.politique_confidentialite(c), c)
    assert "<table>" in html and "<blockquote>" in html
    assert "**" not in html
    assert not re.search(r"\[[^\]]+\]\([^)]+\)", html), "lien markdown non converti"
    assert 'name="robots"' in html, "ces pages n'ont pas à être indexées"


def test_les_routes_legal_repondent(conf):
    from fastapi.testclient import TestClient

    import agent.main as main

    client = TestClient(main.app)
    for chemin in ("/legal", "/legal/confidentialite", "/legal/suppression",
                   "/legal/cgu", "/legal/mentions", "/legal/ia"):
        r = client.get(chemin)
        assert r.status_code == 200, chemin
        assert "text/html" in r.headers["content-type"]
    assert client.get("/legal/inexistant").status_code == 404


def test_sans_configuration_rien_n_est_publie(tmp_path, monkeypatch):
    """Mieux vaut un 404 qu'un gabarit non rempli servi en public."""
    monkeypatch.chdir(tmp_path)
    import importlib

    import agent.juridique as J

    importlib.reload(J)
    assert J.charger() is None

    from fastapi.testclient import TestClient

    import agent.main as main

    assert TestClient(main.app).get("/legal/confidentialite").status_code == 404


def test_l_exemple_livre_porte_l_avertissement_de_revue():
    """Le fichier d'exemple doit lui-même appeler à la relecture."""
    texte = (RACINE / "config" / "juridique.exemple.yaml").read_text(encoding="utf-8")
    assert "RELUS PAR UN JURISTE OU UN AVOCAT" in texte
    donnees = yaml.safe_load(texte)
    assert donnees["revue_juridique"]["effectuee"] is False, (
        "l'exemple ne doit jamais être livré avec la revue déclarée faite"
    )


def test_la_configuration_juridique_est_exclue_du_depot():
    """
    Elle porte la raison sociale, l'immatriculation, l'adresse et les contacts
    du client. Rien de secret au sens d'une clé, mais rien qui ait à se
    retrouver dans un dépôt public non plus.
    """
    lignes = (RACINE / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "config/juridique.yaml" in lignes


# ── Vérification assistée ────────────────────────────────────────────────


def _conf_valide():
    return {
        "mode": "direct",
        "entreprise": {
            "raison_sociale": "Plomberie Durand SARL",
            "adresse": "3 rue des Lilas, 93100 Montreuil",
            "email": "contact@plomberie-durand.test",
            "representant_legal": "Alex Durand, gérant",
        },
        "protection_donnees": {"email": "donnees@plomberie-durand.test"},
        "juridiction": {
            "pays": "France",
            "pays_code": "FR",
            "autorite_controle": "CNIL",
            "autorite_controle_url": "https://www.cnil.fr/fr/plaintes",
        },
        "publication": {"url_publique": "https://agent.plomberie-durand.test"},
        "hebergeur": {"nom": "Hetzner Online GmbH", "adresse": "Gunzenhausen, Allemagne"},
    }


def test_une_configuration_complete_passe():
    from agent.juridique import verifier

    assert verifier(_conf_valide()) == []


def test_les_valeurs_de_l_exemple_sont_refusees():
    """
    Le piège principal : recopier l'exemple sans le remplir produit des
    documents d'apparence officielle au nom d'une entreprise qui n'existe pas.
    """
    from agent.juridique import verifier

    c = _conf_valide()
    c["entreprise"]["raison_sociale"] = "Maison Lorette"
    problemes = verifier(c)
    assert any("Maison Lorette" in p for p in problemes)


def test_un_champ_obligatoire_vide_est_signale():
    from agent.juridique import verifier

    c = _conf_valide()
    c["entreprise"]["representant_legal"] = ""
    assert any("representant_legal" in p for p in verifier(c))


def test_une_autorite_incoherente_avec_le_pays_est_signalee():
    """
    Une URL d'autorité fausse envoie la personne dans le vide au moment où elle
    exerce un droit — c'est pire que de ne rien indiquer.
    """
    from agent.juridique import verifier

    c = _conf_valide()
    c["juridiction"]["autorite_controle_url"] = "https://ico.org.uk/make-a-complaint/"
    assert any("autorite_controle_url" in p for p in verifier(c))


def test_toutes_les_autorites_ont_une_url_https():
    from agent.juridique import AUTORITES

    for code, (nom, url, loi) in AUTORITES.items():
        assert url.startswith("https://"), f"{code} : URL non HTTPS"
        assert nom and loi, f"{code} : entrée incomplète"


def test_les_zones_a_refondre_sont_documentees():
    """Un pays signalé comme « à refondre » doit expliquer pourquoi."""
    from agent.juridique import AUTORITES, ZONES_A_REFONDRE

    for code, motif in ZONES_A_REFONDRE.items():
        assert code in AUTORITES, f"{code} absent de AUTORITES"
        assert len(motif) > 80, f"{code} : motif trop vague pour être utile"


def test_l_hebergeur_sans_adresse_est_signale():
    """La LCEN impose une adresse joignable pour l'hébergeur."""
    from agent.juridique import verifier

    c = _conf_valide()
    c["hebergeur"]["adresse"] = ""
    assert any("hebergeur.adresse" in p for p in verifier(c))


def test_une_duree_de_conservation_dupliquee_est_refusee():
    """
    Déclarée ici ET dans RETENTION_JOURS, elle se contredit au premier
    changement de configuration.
    """
    from agent.juridique import verifier

    c = _conf_valide()
    c["traitement"] = {"conservation_jours": 30}
    assert any("conservation_jours" in p for p in verifier(c))


# ── Recherche d'entreprise ───────────────────────────────────────────────


def test_les_formes_juridiques_courantes_sont_traduites():
    """
    Écrire « SARL » sur une SAS est une erreur de mentions légales. Un code
    inconnu doit s'avouer, pas se deviner.
    """
    from agent.juridique import forme_juridique

    assert "SAS" in forme_juridique("5710")
    assert "SARL" in forme_juridique("5499")
    assert forme_juridique("") == ""
    inconnu = forme_juridique("9999")
    assert "9999" in inconnu and "confirmer" in inconnu


def test_la_recherche_ne_leve_jamais(monkeypatch):
    """Un annuaire indisponible ne doit pas interrompre une installation."""
    import agent.juridique as j

    def explose(*a, **k):
        raise OSError("réseau coupé")

    monkeypatch.setattr("urllib.request.urlopen", explose)
    assert j.chercher_entreprise("peu importe") == []


def test_la_recherche_normalise_la_reponse(monkeypatch):
    """Le format de l'annuaire ne doit pas fuir dans le reste du kit."""
    import io
    import json

    import agent.juridique as j

    charge = {
        "results": [{
            "nom_complet": "PLOMBERIE DURAND",
            "siren": "123456789",
            "nature_juridique": "5499",
            "etat_administratif": "A",
            "siege": {"siret": "12345678900012", "adresse": "3 RUE DES LILAS 93100 MONTREUIL"},
            "dirigeants": [{"nom": "DURAND", "prenoms": "ALEX", "qualite": "Gérant"}],
        }]
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: io.BytesIO(json.dumps(charge).encode()),
    )
    r = j.chercher_entreprise("Plomberie Durand")[0]
    assert r["raison_sociale"] == "PLOMBERIE DURAND"
    assert r["siret"] == "12345678900012"
    assert "SARL" in r["forme_juridique"]
    assert r["representants"] == ["Alex Durand, Gérant"]


def test_la_cli_charge_le_env(monkeypatch, tmp_path):
    """
    Les autres modules chargent le .env à l'import ; la ligne de commande ne
    passe par aucun d'eux. Sans chargement explicite, --connu et --verifier
    lisent les valeurs par défaut et annoncent « anthropic / 90 jours » alors
    que le déploiement tourne sur autre chose. L'assistant écrirait alors une
    politique de confidentialité qui contredit la configuration réelle.
    """
    import inspect

    import agent.juridique as j

    source = inspect.getsource(j._cli)
    assert "load_dotenv" in source, "la CLI doit charger le .env comme le fait l'app"


# ── Les trois états de la revue ──────────────────────────────────────────


def _ctx(revue: dict):
    from agent.juridique import contexte

    return contexte({"revue_juridique": revue, "hebergeur": {}})


def test_sans_decision_le_bandeau_est_affiche():
    """
    L'état par défaut. Le bandeau n'est pas une punition : il empêche qu'un
    document sorte sans que personne ait tranché.
    """
    from agent.juridique import _bandeau

    assert "non relu" in _bandeau(_ctx({})).lower()


def test_apres_relecture_le_bandeau_disparait():
    from agent.juridique import _bandeau

    assert _bandeau(_ctx({"effectuee": True, "par": "Cabinet X"})) == ""


def test_publication_assumee_retire_le_bandeau():
    """
    Publier sans relecture est un choix légitime. Un bandeau d'avertissement
    sur les CGU d'un artisan inquiète ses clients sans les protéger.
    """
    from agent.juridique import _bandeau

    c = _ctx({"publication_assumee": {"acceptee": True, "par": "M. Durand, gérant",
                                      "date": "2026-08-24"}})
    assert _bandeau(c) == ""
    assert c["risque_assume"] is True
    assert c["assume_par"] == "M. Durand, gérant"


def test_une_acceptation_non_cochee_laisse_le_bandeau():
    """Le bloc présent mais acceptee=false ne vaut pas décision."""
    from agent.juridique import _bandeau

    c = _ctx({"publication_assumee": {"acceptee": False, "par": "", "date": ""}})
    assert "non relu" in _bandeau(c).lower()


def test_le_bandeau_disparait_de_tous_les_documents():
    """Un document oublié afficherait le bandeau alors que la décision est prise."""
    from agent.juridique import DOCUMENTS, contexte

    base = {
        "entreprise": {"raison_sociale": "X SARL", "adresse": "1 rue A",
                       "email": "a@b.test", "representant_legal": "Y, gérant",
                       "forme_juridique": "SARL", "immatriculation": "SIREN 1"},
        "protection_donnees": {"email": "a@b.test", "nom": "Y"},
        "juridiction": {"pays": "France", "autorite_controle": "CNIL",
                        "autorite_controle_url": "https://www.cnil.fr/fr/plaintes",
                        "droit_applicable": "droit français", "tribunal": "Paris"},
        "publication": {"url_publique": "https://x.test"},
        "hebergeur": {"nom": "H", "adresse": "ailleurs", "pays_donnees": "France"},
        "traitement": {"finalites": ["a"], "base_legale": "interet_legitime",
                       "donnees_traitees": ["b"], "opt_in": "le client écrit"},
        "integrateur": {"raison_sociale": "I SAS", "adresse": "2 rue B", "email": "i@b.test"},
        "mode": "agence",
    }
    assume = dict(base, revue_juridique={
        "publication_assumee": {"acceptee": True, "par": "Y", "date": "2026-08-24"}})
    sans = dict(base, revue_juridique={})

    for cle, (titre, fn) in DOCUMENTS.items():
        assert "non relu" not in fn(contexte(assume)).lower(), f"{cle} affiche encore le bandeau"
        assert "non relu" in fn(contexte(sans)).lower(), f"{cle} n'affiche pas le bandeau par défaut"
