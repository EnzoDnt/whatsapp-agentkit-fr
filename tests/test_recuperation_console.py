"""
Récupération d'accès à la console.

changer_mot_de_passe() exige l'ancien : correct pour quelqu'un de connecté,
sans issue pour quelqu'un enfermé dehors. Sans voie de secours, un mot de passe
oublié rendait la console définitivement inaccessible — il fallait ouvrir la
base à la main, ce qu'on ne demande pas à quelqu'un qui n'est pas développeur.
"""

import asyncio
import sys

import pytest


def _auth(env_propre):
    return sys.modules["agent.auth"]


def _memoire(env_propre):
    return sys.modules["agent.memory"]


@pytest.fixture()
def compte(env_propre):
    """Un compte existant, dont le mot de passe est réputé oublié."""
    auth = _auth(env_propre)

    async def _creer():
        await _memoire(env_propre).initialiser_base()
        return await auth.creer_utilisateur("a@b.test", "Alex", "AncienMotDePasse1")

    return auth, asyncio.run(_creer())


def test_reinitialiser_permet_de_se_reconnecter(compte):
    """Le cas réel : on a oublié, on repose, on rentre."""
    auth, _ = compte

    assert asyncio.run(auth.authentifier("a@b.test", "AncienMotDePasse1")) is not None
    asyncio.run(auth.reinitialiser_mot_de_passe("a@b.test", "NouveauMotDePasse2"))

    assert asyncio.run(auth.authentifier("a@b.test", "AncienMotDePasse1")) is None
    assert asyncio.run(auth.authentifier("a@b.test", "NouveauMotDePasse2")) is not None


def test_un_compte_desactive_est_reactive(compte):
    """
    Réinitialiser sans réactiver laisserait la personne dehors sans lui dire
    pourquoi : le mot de passe serait bon, et la connexion refusée quand même.
    """
    auth, ident = compte

    # Désactivation directe en base : le kit refuse qu'on se désactive
    # soi-même, et le cas réel est celui d'un compte désactivé par un autre
    # administrateur, ou resté inactif après une reprise.
    async def _desactiver():
        memoire = sys.modules["agent.memory"]
        async with memoire.Session() as session:
            u = await session.get(auth.Utilisateur, ident)
            u.actif = False
            await session.commit()

    asyncio.run(_desactiver())
    assert asyncio.run(auth.authentifier("a@b.test", "AncienMotDePasse1")) is None

    asyncio.run(auth.reinitialiser_mot_de_passe("a@b.test", "NouveauMotDePasse2"))
    assert asyncio.run(auth.authentifier("a@b.test", "NouveauMotDePasse2")) is not None


def test_un_email_inconnu_est_signale(compte):
    auth, _ = compte
    with pytest.raises(ValueError, match="Aucun compte"):
        asyncio.run(auth.reinitialiser_mot_de_passe("absent@b.test", "MotDePasseLong12"))


def test_un_mot_de_passe_trop_court_est_refuse(compte):
    auth, _ = compte
    with pytest.raises(ValueError, match="trop court"):
        asyncio.run(auth.reinitialiser_mot_de_passe("a@b.test", "court"))


def test_l_email_est_normalise(compte):
    """Une majuscule ou un espace ne doit pas bloquer une récupération."""
    auth, _ = compte
    asyncio.run(auth.reinitialiser_mot_de_passe("  A@B.TEST  ", "NouveauMotDePasse2"))
    assert asyncio.run(auth.authentifier("a@b.test", "NouveauMotDePasse2")) is not None


def test_lister_montre_les_comptes(compte):
    """Lever le doute sur l'adresse : la console ne le dira jamais."""
    auth, _ = compte
    comptes = asyncio.run(auth.lister_utilisateurs_console())
    assert [c["email"] for c in comptes] == ["a@b.test"]
    assert comptes[0]["actif"] is True


def test_le_mot_de_passe_n_est_pas_un_argument_de_ligne_de_commande(env_propre):
    """
    Un mot de passe passé en argument reste dans l'historique du shell et dans
    la liste des processus, visible par tout autre utilisateur de la machine.
    """
    import inspect

    source = inspect.getsource(_auth(env_propre)._cli)
    assert "getpass" in source
    assert "--mot-de-passe" not in source and "--password" not in source


def test_la_recuperation_exige_que_le_jeton_soit_defini(env_propre):
    """
    ADMIN_TOKEN doit être PRÉSENT dans l'environnement, sans être ressaisi.

    Le ressaisir n'apportait rien — la commande tourne dans le conteneur, où
    la variable est lisible en clair — et cassait tout usage depuis un terminal
    web. L'exiger reste utile : sur une machine où elle n'est pas définie, on
    n'est pas là où on croit être.
    """
    import inspect

    source = inspect.getsource(_auth(env_propre)._cli)
    assert 'os.getenv("ADMIN_TOKEN"' in source
    assert "opération refusée" in source


# ── Terminaux sans TTY (Coolify, Portainer, docker exec sans -t) ─────────


def test_le_jeton_n_est_plus_redemande(env_propre):
    """
    getpass a besoin d'un vrai TTY. Les terminaux web n'en exposent pas : il y
    lisait du vide, et la commande répondait « Jeton incorrect » quoi que l'on
    tape — exactement là où on en a besoin, c'est-à-dire enfermé dehors sans
    autre accès que ce terminal.

    Le redemander était de toute façon illusoire : la commande s'exécute dans
    le conteneur, où « env | grep ADMIN_TOKEN » l'affiche en clair.
    """
    import inspect

    source = inspect.getsource(_auth(env_propre)._cli)
    assert 'getpass.getpass("ADMIN_TOKEN' not in source
    assert "compare_digest" not in source
    # La variable doit rester EXIGÉE, seulement plus ressaisie.
    assert 'os.getenv("ADMIN_TOKEN"' in source


def test_le_mot_de_passe_peut_venir_de_l_environnement(env_propre):
    """Seule voie possible quand le terminal n'a pas de TTY."""
    import inspect

    source = inspect.getsource(_auth(env_propre)._cli)
    assert "AGENTKIT_NOUVEAU_MDP" in source


def test_sans_tty_et_sans_variable_la_commande_explique(env_propre):
    """
    Un blocage silencieux est le pire des cas : l'utilisateur attend devant un
    curseur sans savoir que rien ne viendra. La commande doit refuser et dire
    comment s'y prendre.
    """
    import inspect

    source = inspect.getsource(_auth(env_propre)._cli)
    assert "isatty" in source
    assert "AGENTKIT_NOUVEAU_MDP=" in source, "le message doit montrer la commande à relancer"


def test_le_mot_de_passe_reste_hors_des_arguments(env_propre):
    """
    Un argument de ligne de commande s'affiche dans la liste des processus de
    toute la machine. Une variable d'environnement est visible du seul
    processus et de root — moins bien qu'un TTY, acceptable pour un dépannage.
    """
    import inspect

    source = inspect.getsource(_auth(env_propre)._cli)
    assert "--mot-de-passe" not in source and "--password" not in source
