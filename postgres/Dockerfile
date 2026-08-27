FROM postgres:latest

COPY certs/server.crt certs/server.key /etc/postgresql/certs/

RUN chown postgres:postgres /etc/postgresql/certs/server.crt /etc/postgresql/certs/server.key \
    && chmod 600 /etc/postgresql/certs/server.key \
    && chmod 644 /etc/postgresql/certs/server.crt
