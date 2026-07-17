"""Paiement de l'abonnement Premium via Stripe Checkout.

Flux : le client clique "Passer Premium" -> on crée une Checkout Session
Stripe (abonnement mensuel) -> il paie sur la page hébergée par Stripe ->
Stripe appelle notre webhook -> on active is_premium en base.

Pas de résiliation en libre-service : aucun Customer Portal n'est branché
ici, volontairement (choix produit). Une resiliation demanderait un
contact direct, ou un ajout ultérieur du Portail Client Stripe.
"""

import os

import stripe

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

stripe.api_key = STRIPE_SECRET_KEY


def is_stripe_configured() -> bool:
    return bool(STRIPE_SECRET_KEY and STRIPE_PRICE_ID)


def create_checkout_session(user_id: str, user_email: str, success_url: str, cancel_url: str) -> str:
    """Crée une Checkout Session Stripe pour l'abonnement Premium et retourne son URL.

    `user_id` est glissé dans `client_reference_id` et dans les metadata de
    l'abonnement : c'est ce qui permet au webhook de retrouver quel compte
    FacelessAI activer en Premium une fois le paiement confirmé.
    """
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        customer_email=user_email,
        client_reference_id=user_id,
        subscription_data={"metadata": {"user_id": user_id}},
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session.url


def parse_webhook_event(payload: bytes, signature: str):
    """Vérifie la signature Stripe et retourne l'événement, ou lève une exception si invalide."""
    return stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
