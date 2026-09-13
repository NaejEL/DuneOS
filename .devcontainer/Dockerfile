# Same tag as the `container:` of every CI job in
# .github/workflows/ci.yml — a bump in one without the other means the
# devcontainer stops being the environment CI uses. A test asserts it.
ARG DOCKER_TAG=v6.0.1
FROM espressif/idf:${DOCKER_TAG}

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

RUN apt-get update -y && apt-get install udev -y

RUN echo "source /opt/esp/idf/export.sh > /dev/null 2>&1" >> ~/.bashrc

ENTRYPOINT [ "/opt/esp/entrypoint.sh" ]

CMD ["/bin/bash", "-c"]