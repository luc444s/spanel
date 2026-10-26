<?php
/**
 * A.SPEC 0033 — un solo phpMyAdmin para todas las bases.
 *
 * El contenedor corre con PMA_ARBITRARY=1: el servidor viaja en el POST de
 * login (`pma_servername`). Este archivo limita qué nombres se aceptan para que
 * nadie use el hub como salto hacia un host arbitrario.
 *
 * Acepta el MariaDB heredado (`mariadb`) y los contenedores por sitio
 * (`spanel-<slug>-db`, `legalattorney-clean-db`, cualquier `*-db`). Un esquema,
 * una IP o un nombre fuera de ese patrón se rechazan antes de autenticar.
 */
$cfg['AllowArbitraryServer'] = true;
$cfg['ArbitraryServerRegexp'] = '/^(?:mariadb|[a-z0-9][a-z0-9_.-]*-db)$/';
