# Le numéro de téléphone WhatsApp

> À lire **avant** de promettre une date à un client. Le numéro est la seule
> partie du setup qui dépend d'un tiers (l'opérateur, puis Meta), donc la seule
> qui prend un délai que tu ne maîtrises pas. Anticipe-la.

L'agent tourne sur la **WhatsApp Cloud API** de Meta. Elle a une exigence que
les gens découvrent souvent trop tard : il faut un **numéro de téléphone dédié**,
que la plateforme prend en main. Ce n'est pas immédiat, et ça ne peut pas être
n'importe quel numéro.

Si tu es l'agent de dev qui accompagne l'installation : **explique ce point dès
le début de la mise en production**, pas au moment où tu es bloqué dessus. Une
phrase suffit — « Pour passer en vrai sur WhatsApp, il nous faut un numéro dédié,
compte quelques jours pour l'avoir et le faire valider par Meta. »

---

## La règle absolue : un numéro neuf, jamais celui du client

Le numéro que tu vas connecter **ne doit pas déjà être utilisé sur WhatsApp** —
ni l'application WhatsApp classique, ni l'application WhatsApp Business. La Cloud
API prend le contrôle du numéro : une fois branché, il ne s'utilise plus depuis
un téléphone.

Donc **jamais le numéro personnel du client**, ni le tien. Il faut une ligne à
part, qui servira uniquement à l'agent.

Ce numéro doit :

- pouvoir **recevoir un SMS ou un appel vocal** — Meta envoie un code à 6 chiffres
  pour le vérifier ;
- **ne pas être déjà rattaché à un compte WhatsApp** (sinon, supprime d'abord ce
  compte, puis attends quelques minutes) ;
- de préférence être un **+33** si le client est en France : les clients font plus
  confiance à un numéro français, et ça évite les frictions.

---

## Deux façons de l'obtenir

### Recommandé — une ligne VoIP OVHcloud (à partir de ~0,99 €/mois HT)

C'est la voie que nous conseillons chez ZénithIA : économique, dédiée, sans carte
SIM physique à gérer.

1. 🔴 Commande une ligne (un numéro) chez **OVHcloud Télécom**. Une ligne simple
   suffit, l'entrée de gamme tourne autour de 0,99 €/mois HT (tarif indicatif,
   vérifie l'offre du moment).
2. 🔴 Installe un **softphone** sur ton ordinateur ou ton téléphone — une appli qui
   reçoit les appels de la ligne VoIP. **Zoiper** ou **Linphone** font le travail,
   tous deux gratuits. Connecte-le avec les identifiants SIP fournis par OVHcloud.
3. 🔴 Au moment d'ajouter le numéro dans Meta (voir plus bas), choisis la
   vérification **par appel vocal** : Meta appelle la ligne et **dicte le code**,
   que tu lis dans le softphone. Si l'option SMS est disponible sur ta ligne, elle
   marche aussi.

> Pourquoi l'appel vocal : toutes les lignes VoIP ne reçoivent pas les SMS, mais
> elles reçoivent les appels. L'option « Call me » de Meta lit le code à voix
> haute — c'est la méthode la plus fiable sur une ligne VoIP.

### Plus simple — un numéro physique bas coût

Si la VoIP te rebute, prends une **carte SIM dédiée** chez un opérateur bon marché
(un forfait **Free** à ~2 €/mois, ou une SIM prépayée). Tu la gardes uniquement
pour ce compte.

- Avantage : le code de vérification arrive **par SMS**, sans rien configurer.
- Contrainte : c'est une SIM de plus à garder quelque part et à ne pas résilier
  tant que l'agent tourne.

Dans les deux cas, l'important est le même : **un numéro à part, capable de
recevoir le code**, et de préférence en +33.

---

## Une fois le numéro en main

1. 🔴 Dans le tableau de bord Meta → **WhatsApp → API Setup** (ou **Gérer les
   numéros de téléphone**), ajoute le numéro et lance la vérification. Saisis le
   code reçu (SMS ou appel).
2. 🔴 Renseigne le **nom affiché** de l'entreprise (celui que verront les clients).
   Meta le passe en **revue** : c'est une source de délai courante, prévois-le.
3. ✅ Le numéro validé te donne un **Phone Number ID** — c'est lui qui va dans
   `META_PHONE_NUMBER_ID`. La suite du branchement est dans **`SETUP-META.md`**.

**Un mot sur le volume.** Un numéro neuf démarre avec une limite (souvent 250
conversations par 24 h). Elle **augmente automatiquement** avec l'usage et la
qualité des échanges. Pour un premier client, c'est bien plus que suffisant —
mais dis-le, pour que personne ne s'inquiète en voyant la limite au départ.

---

## Pour voir l'agent AVANT d'avoir un numéro

Pas besoin d'attendre le numéro pour montrer l'agent. Deux options :

- **Le simulateur local** du kit : tu discutes avec l'agent dans ton terminal ou
  ton navigateur, sans aucun compte WhatsApp. Idéal pour valider le comportement.
- **Le numéro de test Meta** : gratuit, fourni dans la console, mais il ne peut
  écrire qu'à **5 numéros pré-enregistrés**. Parfait pour une démo à toi-même ou au
  client, jamais pour de vrais clients.

Garde le vrai numéro pour le passage en production — quand le client a dit oui.
