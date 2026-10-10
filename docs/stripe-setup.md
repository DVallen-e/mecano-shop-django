# Configuration Stripe

## Variables d’environnement

Configure ces variables dans l’environnement de l’application, sans les ajouter
au dépôt :

- `STRIPE_SECRET_KEY` : clé secrète Stripe de test (`sk_test_...`) pendant le
  développement, puis clé live en production.
- `STRIPE_WEBHOOK_SECRET` : secret de signature du webhook (`whsec_...`).

Après toute modification des modèles, appliquer les migrations :

```sh
python manage.py migrate
```

## Webhook local

Installer Stripe CLI, puis lancer l’application et transférer les événements
vers :

```sh
stripe listen \
  --events checkout.session.completed,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed,checkout.session.expired,payment_intent.payment_failed,payment_intent.succeeded \
  --forward-to localhost:8000/payments/webhook/stripe/
```
Utiliser la valeur `whsec_...` affichée par Stripe CLI comme
`STRIPE_WEBHOOK_SECRET` en local. L’endpoint vérifie la signature; ne pas
désactiver cette vérification.

## Endpoint de production

Dans Stripe, déclarer l’endpoint HTTPS
`https://<domaine>/payments/webhook/stripe/` et écouter les événements :

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `checkout.session.expired`
- `payment_intent.payment_failed`
- `payment_intent.succeeded`

Le checkout crée la commande et réserve le stock avant de rediriger directement
le client vers Stripe. Le webhook, et non la redirection de retour du navigateur,
met à jour le statut de paiement et confirme une commande. Les sessions expirées
ou paiements échoués libèrent la réservation; un nouvel essai la réserve de
nouveau. Un refus carte (`payment_intent.payment_failed`) est enregistré sans
fermer la session Checkout ni libérer le stock: le client peut réessayer dans
Checkout. La confirmation de paiement reste fondée sur `checkout.session.completed`.

Dans l’admin des commandes, l’action « Expire Stripe sessions pending over
10 hours and release stock » récupère la session auprès de Stripe. Elle ne libère
le stock que si Stripe confirme son expiration; les sessions terminées ou les
erreurs Stripe ne libèrent pas le stock.

## Apple Pay et Google Pay

Activer les moyens de paiement correspondants dans le Dashboard Stripe.
Stripe Checkout les présente lorsque le navigateur/appareil et le compte sont
éligibles. En production, utiliser HTTPS et compléter la vérification du
domaine demandée par Stripe pour les wallets. La collecte d’adresse de livraison
est actuellement limitée à la France.
