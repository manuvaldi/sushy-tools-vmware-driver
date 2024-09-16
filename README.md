# Sushy-Tools with VMWARE Driver

This repo comes from the [fork](https://github.com/ppetrou/sushy-tools-vmware-driver) . It's a sushy-tool modification which supports VMWARE and Vsphere directly.

KUDOS https://github.com/ppetrou/sushy-tools-vmware-driver

My contributions to the code:
- Containerfile and image build
- Update some dependencies
- Update pyvmomi for supporting vsphere 8.x
- CLI deployment
- K8s Deployment
- Show VM Names instead of UUID in `/redfish/v1/Systems/`

## Image

You can use the image from `quay.io/mvalledi/sushy-tools-vmware:latest`

## Standalone use

- Create a `sushy-emulator.conf` config file. Example:

```
SUSHY_EMULATOR_LISTEN_IP = u'0.0.0.0'
SUSHY_EMULATOR_LISTEN_PORT = 8000
SUSHY_EMULATOR_SSL_CERT = None
SUSHY_EMULATOR_SSL_KEY = None
SUSHY_EMULATOR_OS_CLOUD = None
SUSHY_EMULATOR_VMWARE_DRIVER = True
# vmware ESX or vSphere
SUSHY_EMULATOR_VMWARE_HOST = u'10.10.10.10'
SUSHY_EMULATOR_VMWARE_PORT = 443
SUSHY_EMULATOR_VMWARE_USERNAME = u'xxxx@vsphere.local'
SUSHY_EMULATOR_VMWARE_PASSWORD = u'xxxxxxxx'
SUSHY_EMULATOR_VMWARE_VMEDIA_DATASTORE = u'nvme-datastore'
# The following is required to be "True" for ZTP installations
SUSHY_EMULATOR_IGNORE_BOOT_DEVICE = True
SUSHY_EMULATOR_BOOT_LOADER_MAP = {
    u'UEFI': {
        u'x86_64': u'/usr/share/OVMF/OVMF_CODE.secboot.fd'
    },
    u'Legacy': {
        u'x86_64': None
    }
}
```    
- Run

```
podman run -d -p8000:8000 --name sushy-vmware -v $PWD:/etc/sushy quay.io/mvalledi/sushy-tools-vmware:latest sushy-emulator -i ::  --config /etc/sushy/sushy-emulator.conf
```

## k8s Deployment

Look file in deployment folder






## Redfish development tools


This is a set of simple simulation tools aiming at supporting the
development and testing of the Redfish protocol implementations and,
in particular, Sushy library (https://docs.openstack.org/sushy/).

The package ships two simulators - static Redfish responder and
virtual Redfish BMC that is backed by libvirt or OpenStack cloud.

The static Redfish responder is a simple REST API server which
responds the same things to client queries. It is effectively
read-only.

The virtual Redfish BMC resembles the real Redfish-controlled bare-metal
machine to some extent. Some client queries are translated to commands that
actually control VM instances simulating bare metal hardware. However some
of the Redfish commands just return static content never touching the
virtualization backend and, for that matter, virtual Redfish BMC is similar
to the static Redfish responser.

* Free software: Apache license
* Documentation: https://docs.openstack.org/sushy-tools
* Source: http://opendev.org/openstack/sushy-tools
* Bugs: https://storyboard.openstack.org/#!/project/openstack/sushy-tools
