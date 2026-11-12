# Instalador y descubrimiento de infraestructura

## Inventariar el servidor funcional

`discover-infra.sh` inspecciona el host y Docker en modo de solo lectura. Genera
un paquete privado con la topología necesaria para preparar una réplica, pero no
hace backup de volúmenes ni exporta valores secretos.

```bash
sudo ./installer/discover-infra.sh --output /root/spanel-infra
```

Para copiar además los archivos Compose detectados y comprimir el informe:

```bash
sudo ./installer/discover-infra.sh \
  --output /root/spanel-infra \
  --copy-compose \
  --archive
```

`--copy-compose` es explícito porque un YAML puede contener contraseñas inline.
El script nunca copia `.env`, claves privadas, contenido de volúmenes, bases de
datos ni buzones. Esos datos deben respaldarse de forma consistente y enviarse
cifrados según `replication/README.md` dentro del informe.

Antes de compartir el paquete, revise especialmente:

- `host/ip-addresses.txt`;
- `docker/labels/`;
- `compose-files/`, si se solicitó;
- rutas y dominios incluidos en los inventarios.

El script necesita Bash y acceso al daemon Docker. Herramientas como `ss`,
`systemctl`, `ufw`, `nft` y `openssl` son opcionales; cuando existen se obtiene
un diagnóstico más completo.
