# Deux exemples complets

Le kit livre deux configurations métier de bout en bout. Elles ne servent pas
seulement d'illustration : elles montrent **comment écrire** les fichiers pour
que l'agent s'en serve correctement.

| | Maison Lorette | Mario les Bons Tuyaux |
|---|---|---|
| Métier | Boulangerie-pâtisserie | Plomberie, chauffage, sanitaire |
| Où | `config/*.exemple.yaml` | `exemples/plomberie/` |
| Forme | Minimale, pour démarrer | Complète, sept documents |
| Ce qu'elle montre | La structure des fichiers | La rédaction pour un vrai déploiement |

Les deux entreprises sont fictives. Coordonnées, SIRET et numéros d'assurance
sont neutralisés. Seul le numéro d'urgence GRDF (0 800 47 33 33) est réel : le
fausser dans un exemple de consigne de sécurité serait dangereux.

---

## Ce que l'exemple plomberie montre en plus

**Une zone d'intervention.** Un boulanger vend à qui vient ; un plombier se
déplace, et doit refuser ce qui est trop loin. `04-zone-intervention.md` liste
les communes couvertes **et** les communes refusées, code postal par code
postal. Sans la seconde liste, l'agent ne sait pas dire non — il laisse espérer
un déplacement que l'entreprise refusera.

**Des urgences et des consignes de sécurité.** `06-urgences-securite.md`
distingue ce qui est une urgence de ce qui n'en est pas, et donne les gestes de
mise en sécurité. C'est le cas qui justifie que l'escalade vers un humain ne
puisse jamais effacer un message de sécurité.

**Un périmètre métier explicite.** La section « Ce que nous ne faisons pas » est
au moins aussi utile que le catalogue : elle évite que l'agent accepte une
demande hors compétence par complaisance.

**Une équipe.** `02-equipe-disponibilites.md` permet de répondre à « qui
s'occupe de… » sans promettre un intervenant nommé, ce que le planning
n'autorise pas.

---

## La règle de rédaction qui décide de tout

La recherche dans `knowledge/` est **littérale et travaille ligne par ligne**.
Elle renvoie les lignes contenant les mots de la requête. Deux conséquences,
et l'exemple plomberie les applique partout :

**1. Un fait tient sur UNE ligne.**

```
✗  | Dégorgement WC ou évier | 149 EUR |     ← intitulé et prix sur des lignes
   (tableau markdown sur plusieurs lignes)      différentes : la recherche
                                                renvoie l'un sans l'autre

✓  Degorgement debouchage WC toilettes evier bouche : 149 EUR TTC a partir de.
```

Quand le prix et son intitulé se séparent, le modèle reçoit une moitié
d'information — et comble le vide. C'est-à-dire qu'il invente un tarif.

**2. Il n'y a pas de racinisation.**

« déboucher » ne trouve pas « débouchage », et « chaudière » ne trouve pas
« chaudiere ». Écrivez les variantes utiles sur la même ligne, accentuées et
non accentuées, avec les synonymes que vos clients emploient réellement :

```
Degorgement debouchage deboucher debouche WC toilettes cuvette sanitaire
evier lavabo douche bouche bouchee engorge : 149 EUR TTC a partir de.
```

Ce n'est pas élégant à lire. Ce n'est pas fait pour être lu par un humain :
c'est fait pour être trouvé.

---

## S'en servir

```bash
cp exemples/plomberie/entreprise.yaml config/entreprise.yaml
cp exemples/plomberie/prompts.yaml     config/prompts.yaml
cp exemples/plomberie/knowledge/*.md   knowledge/
```

Puis remplacez le contenu par celui de votre entreprise, en gardant la forme.
`config/` et `knowledge/` sont exclus du dépôt public par le `.gitignore` : vos
tarifs et vos documents restent chez vous.

## La répartition entre les fichiers

| Fichier | Contient | Exemple |
|---|---|---|
| `knowledge/*.md` | Les faits — ce que l'entreprise sait | « Entretien chaudière : 159 EUR » |
| `config/prompts.yaml` | La conduite — comment elle se comporte | « Vérifie la zone avant tout » |
| `config/entreprise.yaml` | Les contraintes — ce qui ne se négocie pas | `delais_heures: chantier: 72` |
| Console → Consignes | L'éphémère — ce qui change cette semaine | « Sofia est en congés jusqu'au 1er septembre » |

Seul `delais_heures` est lu par le code, via l'outil `verifier_delai` : c'est le
point où un délai intenable est refusé quelle que soit l'insistance du client.
Le reste de `entreprise.yaml` documente l'entreprise pour les humains qui
reprendront le fichier.
