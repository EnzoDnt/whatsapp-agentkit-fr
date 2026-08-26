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


def test_la_recuperation_exige_le_jeton_d_installation(env_propre):
    """Sans garde-fou, la commande serait une porte dérobée."""
    import inspect

    source = inspect.getsource(_auth(env_propre)._cli)
    assert "ADMIN_TOKEN" in source
    assert "compare_digest" in source
