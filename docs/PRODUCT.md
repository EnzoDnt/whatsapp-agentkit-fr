# PRODUCT.md

## Register

**Product.** La console sert le travail : lire ce que l'agent a répondu, reprendre
la main, corriger son comportement. Le design disparaît derrière la tâche.

## Users

**Principal — le dirigeant de PME.** Boulanger, gérant de salon, kiné, garagiste.
Non technique, et il ne le deviendra pas. Il ouvre la console quelques minutes par
jour, entre deux tâches : *« qui a écrit ? y a-t-il une commande ? qu'est-ce que
l'agent a raconté ? »*. Sur ordinateur au bureau ou dans l'arrière-boutique, mais
aussi sur son téléphone, debout, entre deux clients. **Le mobile n'est pas une
dégradation : c'est un second usage principal.**

**Secondaire — l'équipe.** Une ou deux personnes qui répondent aux clients quand
l'agent ne suffit plus. D'où des comptes nommés : on doit savoir qui a repris la
main, qui a modifié les instructions.

**Tertiaire — l'intégrateur.** L'agence qui installe et configure l'agent pour son
client, puis lui remet les clés.

## Product purpose

Piloter un agent WhatsApp sans écrire une ligne de code ni ouvrir un fichier.
Cinq tâches, dans cet ordre d'importance :

1. Lire les conversations et **reprendre la main** quand il le faut
2. Voir les **demandes** que l'agent a enregistrées
3. Poser des **consignes ponctuelles**, comme on briefe un employé le matin
4. Tenir à jour les **documents** dans lesquels l'agent puise
5. Ajuster son **comportement** général

## Brand personality

Neutre chez le client, avec une signature discrète « propulsé par ZénithIA » en
pied de barre latérale. Le dirigeant doit voir *sa* console, pas l'outil d'un
prestataire.

Trois mots : **calme, direct, artisanal.** Un outil de métier, pas une démo de
technologie. Le ton écrit vouvoie, va droit au but, n'explique jamais un concept
technique — il le contourne.

## Anti-references

- **Un back-office technique.** Pas de tableaux bruts, pas de jargon, pas d'ID à
  rallonge, pas de logs. Chaque écran se comprend sans traduction.
- **Un CRM d'entreprise.** Ni Salesforce ni HubSpot : pas de menus à tiroirs, pas
  de trente champs, pas de colonnes configurables. Une PME de six personnes doit
  s'y retrouver seule.
- **Un SaaS générique à la mode.** Pas de dégradés violets, pas de grilles de
  cartes identiques, pas de gros chiffre en héro, pas d'illustration 3D. Rien qui
  laisse penser « template ».
- **Une messagerie grand public.** Ce n'est pas un clone de WhatsApp : c'est un
  poste de pilotage qui donne accès à des conversations.

## Strategic design principles

1. **La conversation est le produit.** Tout le reste est réglage. L'écran par
   défaut, c'est ce que les clients ont dit aujourd'hui.
2. **Reprendre la main doit être évident et réversible.** C'est le geste le plus
   important de l'outil, et le plus anxiogène. Il doit être visible en permanence
   et annulable d'un clic.
3. **Aucun écran vide sans pédagogie.** Un état vide explique ce qui apparaîtra
   là et pourquoi. C'est souvent la première chose que voit un nouveau client.
4. **Rien d'irréversible sans confirmation, rien de réversible avec.** Effacer
   demande une confirmation ; désactiver une consigne, non.
5. **Le mobile fait tout ce que fait l'ordinateur.** Pas de fonction réservée au
   grand écran : la reprise de main arrive souvent quand on n'est pas au bureau.

## Accessibility

Contraste AA minimum sur tout texte. Navigation clavier complète (le dirigeant
tape vite, à une main, en tenant autre chose). Cibles tactiles ≥ 44 px sur mobile.
Aucune information portée par la seule couleur : chaque état a un mot.
