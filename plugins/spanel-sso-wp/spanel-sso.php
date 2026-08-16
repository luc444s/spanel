<?php
/**
 * Plugin Name: Spanel SSO
 * Description: Magic link SSO desde Spanel. Valida JWT HS256 y crea sesion WP.
 * Version: 0.1.0
 */

defined('ABSPATH') or exit;

function spanel_sso_b64url_decode($data) {
    return base64_decode(strtr($data, '-_', '+/'));
}

function spanel_sso_verify_jwt($token, $secret) {
    $parts = explode('.', $token);
    if (count($parts) !== 3) return null;
    list($header_b64, $payload_b64, $sig_b64) = $parts;
    $expected = hash_hmac('sha256', $header_b64 . '.' . $payload_b64, $secret, true);
    $given = spanel_sso_b64url_decode($sig_b64);
    if (!hash_equals($expected, $given)) return null;
    $payload = json_decode(spanel_sso_b64url_decode($payload_b64), true);
    if (!is_array($payload)) return null;
    if (!isset($payload['exp']) || $payload['exp'] < time()) return null;
    if (empty($payload['sub']) || strpos($payload['sub'], '@') === false) return null;
    return $payload;
}

add_action('rest_api_init', function () {
    register_rest_route('spanel/v1', '/sso', array(
        'methods' => 'GET',
        'permission_callback' => '__return_true',
        'callback' => function (WP_REST_Request $request) {
            $token = $request->get_param('token');
            $secret = defined('SPANEL_SSO_SECRET')
                ? SPANEL_SSO_SECRET
                : (getenv('SPANEL_SSO_SECRET') ?: trim((string) @file_get_contents(ABSPATH . 'spanel-sso-secret.txt')));
            if (!$secret || !$token) {
                return new WP_REST_Response(array('error' => 'unauthorized'), 401);
            }
            $payload = spanel_sso_verify_jwt((string) $token, (string) $secret);
            if ($payload === null) {
                return new WP_REST_Response(array('error' => 'invalid token'), 401);
            }
            $user = get_user_by('email', sanitize_email($payload['sub']));
            if (!$user) {
                return new WP_REST_Response(array('error' => 'user not found'), 403);
            }
            wp_set_auth_cookie($user->ID, true);
            wp_redirect(admin_url());
            exit;
        },
    ));
});
