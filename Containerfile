FROM quay.io/centos/centos:stream9-minimal

RUN microdnf install -y python3.11 python3-pip \
    && microdnf clean all

COPY . /tmp/src/

RUN PBR_VERSION=0.0.1.dev-vmware-driver pip3 install /tmp/src \
    && rm -Rf /tmp/src

EXPOSE 8000

USER 1001
