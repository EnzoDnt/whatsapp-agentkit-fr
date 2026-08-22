"""
Vérification sur un vrai PostgreSQL.

Le kit annonce PostgreSQL pour la production. Or SQLite et PostgreSQL ne se
comportent pas pareil là où ça compte : SQLite rend des datetime naïfs quand
PostgreSQL rend des datetime avec fuseau, l'introspection PRAGMA n'existe pas
côté PostgreSQL, et les contraintes d'unicité ne remontent pas la même erreur.
Tester uniquement sur SQLite revient à ne pas tester la production.

Les tests sont ignorés si aucun PostgreSQL n'écoute — le kit doit rester
installable sans Docker.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid

import pytest

URL_TEST = os.getenv(
    "POSTGRES_TEST_URL", "postgresql://postgres:test@127.0.0.1:55432/agentkit"
)


def _postgres_disponible() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 55432), timeout=1.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_disponible(),
    reason="aucun PostgreSQL sur 127.0.0.1:55432 (docker run postgres:17-alpine)",
)


@pytest.fixture()
def memoire_pg(tmp_path, monkeypatch):
    """Charge la couche mémoire branchée sur PostgreSQL."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    monkeypatch.setenv("DATABASE_URL", URL_TEST)
    monkeypatch.setenv("PII_HASH_SALT", "sel-pg")
    monkeypatch.setenv("RETENTION_JOURS", "90")

    for nom in ("agent.securite", "agent.llm", "agent.memory", "agent.auth"):
        importlib.reload(sys.modules[nom]) if nom in sys.modules else importlib.import_module(nom)
    return sys.modules["agent.memory"]


def executer(memoire, scenario):
    """
    Joue la préparation, le scénario et la fermeture dans UNE seule boucle.

    asyncpg attache ses connexions à la boucle qui les a ouvertes : préparer la
    base dans un `asyncio.run` puis jouer le test dans un autre laisse le pool
    avec des connexions rattachées à une boucle morte, et tout échoue en
    « Event loop is closed ». En production la question ne se pose pas — uvicorn
    tient une boucle unique — mais le banc de test doit refléter ça.
    """
    async def tout():
        async with memoire.moteur.begin() as conn:
            await conn.run_sync(memoire.Base.metadata.drop_all)
        await memoire.initialiser_base()
        try:
            return await scenario()
        finally:
            await memoire.moteur.dispose()

    return asyncio.run(tout())


class TestPostgres:
    def test_l_url_est_reecrite_avec_le_pilote_asynchrone(self, memoire_pg):
        """Les hébergeurs livrent « postgresql:// » ; SQLAlchemy async exige asyncpg."""
        assert memoire_pg.URL_BASE.startswith("postgresql+asyncpg://")

    def test_le_schema_ancien_style_postgres_est_aussi_accepte(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/d")
        importlib.reload(sys.modules["agent.memory"])
        assert sys.modules["agent.memory"].URL_BASE.startswith("postgresql+asyncpg://")

    def test_toutes_les_tables_sont_creees(self, memoire_pg):
        from sqlalchemy import text

        async def lister():
            async with memoire_pg.moteur.begin() as conn:
                r = await conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'"))
                return {ligne[0] for ligne in r.fetchall()}

        tables = executer(memoire_pg, lister)
        assert {"messages", "evenements_traites", "consignes", "demandes",
                "contacts", "escalades", "conversations_en_pause",
                "utilisateurs"} <= tables

    def test_la_migration_de_colonnes_ne_casse_pas_sur_postgres(self, memoire_pg):
        """PRAGMA n'existe pas côté PostgreSQL : la migration doit s'abstenir sans planter."""
        executer(memoire_pg, memoire_pg.migrer_colonnes)   # ne doit lever aucune exception

    def test_la_deduplication_repose_sur_la_contrainte_d_unicite(self, memoire_pg):
        evenement = f"evt-{uuid.uuid4()}"

        async def scenario():
            premier = await memoire_pg.marquer_evenement_traite(evenement)
            second = await memoire_pg.marquer_evenement_traite(evenement)
            return premier, second

        assert executer(memoire_pg, scenario) == (True, False)

    def test_huit_insertions_simultanees_ne_donnent_qu_un_succes(self, memoire_pg):
        evenement = f"evt-{uuid.uuid4()}"

        async def scenario():
            return await asyncio.gather(
                *[memoire_pg.marquer_evenement_traite(evenement) for _ in range(8)]
            )

        assert sum(executer(memoire_pg, scenario)) == 1

    def test_liberer_un_evenement_autorise_le_reessai(self, memoire_pg):
        evenement = f"evt-{uuid.uuid4()}"

        async def scenario():
            await memoire_pg.marquer_evenement_traite(evenement)
            await memoire_pg.liberer_evenement(evenement)
            return await memoire_pg.marquer_evenement_traite(evenement)

        assert executer(memoire_pg, scenario) is True

    def test_les_consignes_datees_se_comportent_comme_sur_sqlite(self, memoire_pg):
        """
        Le point sensible : PostgreSQL rend des datetime avec fuseau, SQLite non.
        Comparer les deux sans précaution lève « can't compare offset-naive and
        offset-aware datetimes ».
        """
        from datetime import datetime, timedelta, timezone

        maintenant = datetime.now(timezone.utc)

        async def scenario():
            async with memoire_pg.Session() as s:
                s.add_all([
                    memoire_pg.Consigne(texte="active",
                                        debut=maintenant - timedelta(days=1),
                                        fin=maintenant + timedelta(days=1)),
                    memoire_pg.Consigne(texte="programmee",
                                        debut=maintenant + timedelta(days=2)),
                    memoire_pg.Consigne(texte="expiree", fin=maintenant - timedelta(days=2)),
                    memoire_pg.Consigne(texte="permanente"),
                    memoire_pg.Consigne(texte="eteinte", activee=False),
                ])
                await s.commit()
            return [c.texte for c in await memoire_pg.consignes_actives()]

        assert set(executer(memoire_pg, scenario)) == {"active", "permanente"}

    def test_l_historique_reste_ordonne_et_cloisonne(self, memoire_pg):
        async def scenario():
            for i in range(3):
                await memoire_pg.enregistrer_message("33611111111", "user", f"a{i}")
                await memoire_pg.enregistrer_message("33611111111", "assistant", f"b{i}")
            await memoire_pg.enregistrer_message("33622222222", "user", "autre client")
            un = await memoire_pg.obtenir_historique("33611111111")
            deux = await memoire_pg.obtenir_historique("33622222222")
            return un, deux

        un, deux = executer(memoire_pg, scenario)
        assert [m["content"] for m in un] == ["a0", "b0", "a1", "b1", "a2", "b2"]
        assert [m["content"] for m in deux] == ["autre client"]

    def test_l_historique_est_plafonne(self, memoire_pg, monkeypatch):
        async def scenario():
            for i in range(30):
                await memoire_pg.enregistrer_message("336", "user", f"m{i}")
            return await memoire_pg.obtenir_historique("336")

        assert len(executer(memoire_pg, scenario)) <= memoire_pg.MAX_MESSAGES_HISTORIQUE

    def test_la_purge_rgpd_fonctionne(self, memoire_pg):
        from datetime import datetime, timedelta, timezone

        async def scenario():
            async with memoire_pg.Session() as s:
                s.add(memoire_pg.Message(
                    telephone="336", role="user", contenu="vieux",
                    cree_le=datetime.now(timezone.utc) - timedelta(days=200)))
                s.add(memoire_pg.Message(telephone="336", role="user", contenu="recent"))
                await s.commit()
            efface = await memoire_pg.purger_donnees_expirees()
            return efface, await memoire_pg.obtenir_historique("336")

        efface, restants = executer(memoire_pg, scenario)
        assert efface == 1 and [m["content"] for m in restants] == ["recent"]

    def test_les_comptes_et_l_unicite_des_adresses(self, memoire_pg):
        from fastapi import HTTPException

        auth = sys.modules["agent.auth"]

        async def scenario():
            ident = await auth.creer_utilisateur("a@b.fr", "A", "un-mot-de-passe-long")
            u = await auth.authentifier("a@b.fr", "un-mot-de-passe-long")
            faux = await auth.authentifier("a@b.fr", "mauvais-mot-de-passe")
            try:
                await auth.creer_utilisateur("a@b.fr", "B", "un-autre-mot-de-passe")
                doublon = False
            except HTTPException:
                doublon = True
            return ident, u, faux, doublon

        ident, u, faux, doublon = executer(memoire_pg, scenario)
        assert ident and u is not None and faux is None and doublon is True

    def test_les_contacts_survivent_a_une_mise_a_jour_partielle(self, memoire_pg):
        async def scenario():
            await memoire_pg.toucher_contact("336", nom_whatsapp="Léa M.",
                                             username="@leam", pays="FR")
            await memoire_pg.modifier_contact("336", "Léa — traiteur", "Paie à 30 jours")
            await memoire_pg.toucher_contact("336", nom_whatsapp="Léa M.")   # nouveau message
            return await memoire_pg.voir_contact("336")

        c = executer(memoire_pg, scenario)
        assert c.nom == "Léa — traiteur"      # l'édition manuelle n'est pas écrasée
        assert c.notes == "Paie à 30 jours"
        assert c.username == "@leam"

    def test_les_escalades_et_la_pause_fonctionnent(self, memoire_pg):
        async def scenario():
            ref = await memoire_pg.enregistrer_escalade(
                "336", "motif", "question", "brouillon", "haute")
            en_attente = await memoire_pg.escalades_en_attente()
            await memoire_pg.basculer_pause_conversation("336", True)
            pause = await memoire_pg.conversation_en_pause("336")
            await memoire_pg.basculer_pause_conversation("336", False)
            reprise = await memoire_pg.conversation_en_pause("336")
            return ref, en_attente, pause, reprise

        ref, en_attente, pause, reprise = executer(memoire_pg, scenario)
        assert ref and en_attente == 1 and pause is True and reprise is False

    def test_mettre_deux_fois_en_pause_ne_leve_pas_d_erreur(self, memoire_pg):
        async def scenario():
            await memoire_pg.basculer_pause_conversation("336", True)
            await memoire_pg.basculer_pause_conversation("336", True)   # idempotent
            return await memoire_pg.conversation_en_pause("336")

        assert executer(memoire_pg, scenario) is True
