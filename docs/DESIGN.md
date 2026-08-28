# DESIGN.md

## Visual theme

**Atelier, pas cockpit.** Fond papier chaud, encre profonde, un seul accent terre
cuite. L'inverse du tableau de bord sombre à néons : cette console s'ouvre en
plein jour, dans une boutique ou un bureau, entre deux tâches.

Le piège évité volontairement : **le vert WhatsApp**. C'est le premier réflexe pour
tout ce qui touche à WhatsApp, et il fait immédiatement « intégration tierce ». Le
second réflexe, le bleu marine d'outil B2B, fait « CRM ». Ni l'un ni l'autre.

Thème clair par défaut. Une variante sombre suit `prefers-color-scheme` pour ceux
qui règlent leur système ainsi, sans jamais être imposée.

## Color

Stratégie : **Restrained.** Neutres teintés chauds, un seul accent sous les 10 %
de la surface. Espace OKLCH ; aucun neutre n'est un gris pur, tous tirent vers la
teinte 70 (ambre).

| Rôle | Clair | Sombre |
|---|---|---|
| Fond application | `oklch(0.975 0.006 75)` | `oklch(0.19 0.008 70)` |
| Surface | `oklch(0.995 0.004 75)` | `oklch(0.235 0.009 70)` |
| Surface enfoncée | `oklch(0.955 0.008 75)` | `oklch(0.165 0.008 70)` |
| Bordure | `oklch(0.90 0.010 75)` | `oklch(0.31 0.011 70)` |
| Texte | `oklch(0.245 0.016 60)` | `oklch(0.94 0.008 75)` |
| Texte discret | `oklch(0.53 0.017 65)` | `oklch(0.68 0.014 70)` |
| **Accent** (terre cuite) | `oklch(0.545 0.135 42)` | `oklch(0.66 0.125 45)` |
| Accent surface | `oklch(0.955 0.022 55)` | `oklch(0.28 0.038 45)` |

Sémantique, jamais décorative : `succes` vert olive `oklch(0.52 0.10 145)`,
`attente` ambre `oklch(0.62 0.115 75)`, `danger` rouge brique `oklch(0.53 0.155 28)`.
Chaque état porte aussi un mot — la couleur ne dit jamais rien toute seule.

## Typography

Une seule famille : la pile système. Native partout, zéro téléchargement, zéro
dépendance externe (le kit doit tourner hors ligne).

```
-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif
```

Échelle rem fixe, ratio 1.2 : 0.75 / 0.8125 / 0.875 / 1 / 1.125 / 1.375 / 1.75 rem.
Graisses 400 / 500 / 600 / 700. Hiérarchie par graisse et taille, jamais par
couleur seule. Prose plafonnée à 68ch ; les listes denses peuvent aller plus loin.

## Elevation

Deux niveaux, pas plus. Les ombres sont chaudes, jamais noires.

- Repos : `0 1px 2px oklch(0.24 0.02 60 / 0.05)`
- Flottant (menus, panneaux) : `0 8px 24px -6px oklch(0.24 0.02 60 / 0.14)`

Le contour prime sur l'ombre : une bordure 1px porte la séparation dans la
majorité des cas.

## Layout

App shell. Barre latérale 232 px sur ordinateur ; sous 900 px elle devient une
**barre d'onglets en bas**, pouce accessible, cibles 48 px.

Rayons : 8 px sur les contrôles, 12 px sur les panneaux, 999 px sur les pastilles.
Rythme d'espacement variable — 6 / 10 / 14 / 20 / 28 / 40 px — jamais la même
respiration partout.

**Pas de grille de cartes.** Les listes sont des listes : lignes séparées par un
filet, pas des cartes empilées. Aucune carte imbriquée nulle part.

## Components

Chaque contrôle interactif possède ses sept états : repos, survol, focus visible,
actif, désactivé, chargement, erreur. Le focus est un anneau de 2 px accent avec
2 px de décalage — visible sur toutes les surfaces.

Chargement : squelettes aux dimensions du contenu attendu, jamais de rouet centré.
États vides : une phrase qui dit ce qui apparaîtra là, et l'action pour l'amorcer.

## Motion

160 ms sur les transitions courantes, 220 ms sur les panneaux. Courbe
`cubic-bezier(0.22, 1, 0.36, 1)` (ease-out-quart). Aucun rebond, aucune
chorégraphie au chargement. La couleur et l'opacité s'animent ; jamais la mise en
page. Tout est neutralisé sous `prefers-reduced-motion`.
