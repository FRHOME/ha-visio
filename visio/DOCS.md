# Visio

Appel vidéo d'un clic entre ton téléphone et la tablette murale, sans app ni compte.

## Comment ça marche

1. Tu ouvres **Visio** (barre latérale, ou bouton sur ton dashboard) et tu touches **Appeler**.
2. L'app ouvre sa page sur la tablette (Kiosk Satellite), coupe l'écran de veille et fait sonner.
3. La tablette affiche « <ton nom> vous appelle » avec **Répondre** et **Refuser**.
4. L'image et le son passent en direct entre les deux appareils (WebRTC), au travers du réseau local ou de Site Magic.
5. Quand on raccroche (ou après la durée de sonnerie sans réponse), la tablette revient au dashboard et l'écran de veille est restauré.

L'entité `sensor.visio` (`idle` / `ringing` / `active`, attribut `appelant`) permet de faire des automatisations, par exemple monter le volume de la tablette quand ça sonne.

## Réglages

| Option | Rôle |
|---|---|
| `device_name` | Nom affiché côté appelant (« Appeler … »). |
| `device_user_agent` | Regex qui reconnaît le navigateur de la tablette. |
| `device_users` | Ou bien : comptes HA considérés comme « tablette ». |
| `device_ha_url` | URL de HA vue par la tablette. Avec Kiosk Satellite : `http://127.0.0.1:2325` (contexte sécurisé, nécessaire pour la caméra). |
| `open_service` / `close_service` | Services Kiosk Satellite pour ouvrir / fermer la page. |
| `return_button` | Bouton à presser après l'appel pour revenir au dashboard. |
| `turn_off_during_call` | Interrupteurs coupés pendant l'appel puis remis comme avant (veille, détection de mouvement qui tient la caméra). |
| `turn_on_during_call` | Interrupteurs allumés pendant l'appel puis remis comme avant. |
| `ring_duration` | Secondes avant « Pas de réponse ». |
| `ice_servers` | STUN/TURN. Sur un même réseau ou via Site Magic, le STUN suffit. |
| `turn_username` / `turn_password` | Seulement si tu ajoutes un serveur `turn:`. |

## Prérequis

- Côté appelant, HA doit être ouvert en **HTTPS** (URL externe, Nabu Casa, Cloudflare…) : sinon le navigateur refuse la caméra.
- Kiosk Satellite doit avoir l'autorisation Android caméra + micro.
- Un seul appel à la fois.
