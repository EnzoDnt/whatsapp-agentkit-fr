"""
Les documents juridiques vus depuis la console.

Sans cette vue, les documents existaient sur /legal sans que personne ne sache
où, et la décision sur le bandeau se prenait en éditant un fichier YAML à la
main — ce qui n'est pas une opération d'exploitant.
"""

import yaml

JURIDIQUE = """\
mode: direct
entreprise:
  raison_sociale: "Plomberie Durand SARL"
  forme_juridique: "SARL"
  immatriculation: "SIREN 900000001"
  adresse: "3 rue des Lilas, 93100 Montreuil"
  telephone: "01 99 12 34 56"
  email: "contact@durand.test"
  representant_legal: "Alex Durand, gérant"
# Un commentaire qui doit survivre à toute écriture depuis la console.
hebergeur:
  nom: "Hetzner Online GmbH"
  adresse: "Gunzenhausen, Allemagne"
  pays_donnees: "Allemagne"
protection_donnees:
  dpo_designe: false
  nom: "Alex Durand"
  email: "contact@durand.test"
traitement:
  finalites: ["repondre aux demandes"]
  base_legale: "interet_legitime"
  donnees_traitees: ["numero de telephone"]
  opt_in: "le client ecrit le premier"
juridiction:
  pays: "France"
  pays_code: "FR"
  droit_applicable: "droit francais"
  tribunal: "Bobigny"
  autorite_controle: "CNIL"
  autorite_controle_url: "https://www.cnil.fr/fr/plaintes"
publication:
  url_publique: "https://agent.durand.test"
  derniere_revision: "2026-08-26"
revue_juridique:
  effectuee: false
  par: ""
  date: ""
"""


def _installer(env_propre):
    (env_propre / "config" / "juridique.yaml").write_text(JURIDIQUE, encoding="utf-8")


def test_sans_configuration_la_console_explique(connecte):
    r = connecte.get("/admin/juridique")
    assert r.status_code == 200
    d = r.json()
    assert d["configure"] is False
    assert "juridique.exemple.yaml" in d["explication"]


def test_la_console_liste_les_six_documents(env_propre, connecte):
    _installer(env_propre)
    d = connecte.get("/admin/juridique").json()

    assert d["configure"] is True
    assert len(d["documents"]) == 6
    # Les liens doivent être absolus : ils s'ouvrent dans un autre onglet.
    for doc in d["documents"]:
        assert doc["url"].startswith("https://agent.durand.test/legal/")
    assert d["etat"]["decision"] == "aucune"
    assert d["etat"]["bandeau"] is True


def test_assumer_retire_le_bandeau_de_toutes_les_pages(env_propre, connecte):
    _installer(env_propre)

    r = connecte.post("/admin/juridique/decision",
                      json={"decision": "assumee", "par": "Alex Durand, gérant"})
    assert r.status_code == 200

    d = connecte.get("/admin/juridique").json()
    assert d["etat"]["decision"] == "assumee"
    assert d["etat"]["bandeau"] is False
    assert d["etat"]["par"] == "Alex Durand, gérant"

    # L'avertissement de console disparaît ; les pages publiques, elles,
    # n'en portaient déjà aucun.
    assert connecte.get("/admin/juridique").json()["avertissement"] is None
    for cle in ("confidentialite", "cgu", "mentions", "suppression", "ia"):
        page = connecte.get(f"/legal/{cle}")
        assert page.status_code == 200
        assert "non relu" not in page.text.lower()


def test_revenir_en_arriere_remet_l_avertissement_de_console(env_propre, connecte):
    """Une décision doit pouvoir se défaire : c'est un réglage, pas un aller simple."""
    _installer(env_propre)
    connecte.post("/admin/juridique/decision",
                  json={"decision": "assumee", "par": "Alex Durand"})
    assert connecte.get("/admin/juridique").json()["avertissement"] is None

    connecte.post("/admin/juridique/decision", json={"decision": "aucune", "par": ""})
    d = connecte.get("/admin/juridique").json()
    assert d["etat"]["decision"] == "aucune"
    assert d["avertissement"] is not None

    # La page publique, elle, ne porte JAMAIS d'avertissement.
    assert "non relu" not in connecte.get("/legal/confidentialite").text.lower()


def test_une_decision_anonyme_est_refusee(env_propre, connecte):
    """
    Retirer un avertissement juridique engage. La décision doit être nommée,
    sinon personne ne l'assume et la trace ne vaut rien.
    """
    _installer(env_propre)
    r = connecte.post("/admin/juridique/decision", json={"decision": "assumee", "par": ""})
    # Le compte connecté sert de repli : la décision reste attribuée.
    assert r.status_code == 200
    assert connecte.get("/admin/juridique").json()["etat"]["par"]


def test_une_decision_inconnue_est_refusee(env_propre, connecte):
    _installer(env_propre)
    r = connecte.post("/admin/juridique/decision", json={"decision": "peu-importe", "par": "X"})
    assert r.status_code == 400


def test_l_ecriture_preserve_les_commentaires(env_propre, connecte):
    """
    Ce sont les commentaires qui portent les avertissements juridiques. Les
    perdre en cochant une case dans une interface serait le comble.
    """
    _installer(env_propre)
    fichier = env_propre / "config" / "juridique.yaml"
    avant = fichier.read_text(encoding="utf-8")

    connecte.post("/admin/juridique/decision",
                  json={"decision": "relue", "par": "Cabinet Martin"})

    apres = fichier.read_text(encoding="utf-8")
    assert "Un commentaire qui doit survivre" in apres
    assert yaml.safe_load(apres)["entreprise"]["raison_sociale"] == \
        yaml.safe_load(avant)["entreprise"]["raison_sociale"]


def test_la_console_signale_une_configuration_incomplete(env_propre, connecte):
    _installer(env_propre)
    fichier = env_propre / "config" / "juridique.yaml"
    fichier.write_text(
        fichier.read_text(encoding="utf-8").replace(
            'representant_legal: "Alex Durand, gérant"', 'representant_legal: ""'
        ),
        encoding="utf-8",
    )
    d = connecte.get("/admin/juridique").json()
    assert any("representant_legal" in p for p in d["problemes"])


def test_l_acces_exige_une_session(client):
    assert client.get("/admin/juridique").status_code == 401
    assert client.post("/admin/juridique/decision",
                       json={"decision": "assumee", "par": "X"}).status_code == 401
