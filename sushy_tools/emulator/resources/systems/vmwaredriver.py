# Copyright 2021 Red Hat, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from collections import defaultdict
from enum import Enum
import os
import ssl

import requests

from sushy_tools.emulator import constants
from sushy_tools.emulator.resources.systems.base import AbstractSystemsDriver
from sushy_tools import error

vmware_loaded = True

try:
    from pyVim.connect import Disconnect
    from pyVim.connect import SmartConnect
    from pyVmomi import vim
except ImportError:
    vmware_loaded = False

is_loaded = bool(vmware_loaded)


class ErrMsg(object):
    IDENTITY_NOT_FOUND = \
        "VMWAREDRV_ERR_000 - Identity {0} Not Found."

    POWER_STATE_CANNOT_SET = \
        "VMWAREDRV_ERR_010 - Power state {0} for Identity {1} cannot set."

    INVALID_BOOT_SOURCE = \
        "VMWAREDRV_ERR_020 - Boot Source {0} is not valid."

    NO_VIRT_DEV_TO_SUPPORT_BOOT_SRC = \
        ("VMWAREDRV_ERR_021 - No VirtualDevice exists in {0} "
         "to support boot source: {1}.")

    NO_BOOTABLE_DEVICE = \
        ("VMWAREDRV_ERR_022 - No Bootable Device Found. "
         "Cannot get boot device.")

    INVALID_DEVICE_TYPE = \
        ("VMWAREDRV_ERR_023 - Invalid Device Type {0}. "
         "Valid values are: Pxe, Hdd, Cd, Floppy")

    INVALID_BOOT_MODE = \
        ("VMWAREDRV_ERR_030 - Invalid boot mode. "
         "Valid values are UEFI or Legacy.")

    BOOT_IMAGE_CANNOT_BE_SET = \
        ("VMWAREDRV_ERR_031 - Boot image {0} "
         "cannot be set for device {1}")

    ERR_VMWARE_OPEN = \
        "VMWAREDRV_ERR_040 - Error connecting to vmware host. {0}"

    DRV_OP_NOT_SUPPORTED = \
        ("VMWAREDRV_ERR_050 - Operation not supported by "
         "the virtualization driver. VMware API does not support {0}")


class PowerStates(Enum):
    ON = 'On'
    FORCE_ON = 'ForceOn'
    FORCE_OFF = 'ForceOff'
    GRACEFUL_SHUTDOWN = 'GracefulShutdown'
    GRACEFUL_RESTART = 'GracefulRestart'
    FORCE_RESTART = 'ForceRestart'
    NMI = 'Nmi'


class VmwareOpen(object):

    def __init__(self, host, port, username, password):
        self._host = host
        self._port = port
        self._username = username
        self._password = password

    def __enter__(self):
        try:
            sslContext = ssl.create_default_context(
                purpose=ssl.Purpose.CLIENT_AUTH)
            sslContext.verify_mode = ssl.CERT_NONE
            self._service_instance = SmartConnect(
                host=self._host, user=self._username,
                pwd=self._password, port=self._port,
                sslContext=sslContext)
            return self._service_instance

        except IOError as e:
            error_msg = ErrMsg.ERR_VMWARE_OPEN.format(e)
            raise error.FishyError(error_msg)

    def __exit__(self, type, value, traceback):
        Disconnect(self._service_instance)


class VmwareDriver(AbstractSystemsDriver):
    """Vmware driver"""

    def _get_vms(self, service_instance):
        content = service_instance.RetrieveContent()
        # Starting point to look into
        container = content.rootFolder
        # object types to look for
        view_type = [vim.VirtualMachine]
        # whether we should look into it recursively
        recursive = True
        container_view = content.viewManager.CreateContainerView(
            container, view_type, recursive)
        vms = container_view.view

        return vms

    def _get_vm(self, identity, service_instance):

        vmlist = self._get_vms(service_instance)

        for vm in vmlist:
            # NOTE. vCenter supports Virtual Machines with the same name
            #  provided they are on a separate Virtual Machine Folder
            # in a Datacener.
            # This complicates the search by name as we have not other input
            # to further filter the results.
            # At this point the first VM with the matching name will be
            # returned and we assume two vms will not be named the same
            # within a vSphere host due to naming conventions.

            vm_name = vm.summary.config.name
            vm_uuid = vm.summary.config.uuid

            if (vm_name == identity or vm_uuid == identity):
                return vm

        raise error.FishyError(
            ErrMsg.IDENTITY_NOT_FOUND.format(
                identity))

    # Helper method to upload an image to the hypervisor
    # PLEASE NOTE! This method is not covered by the Unit Tests at this point,
    # due to complexity.
    # It should NOT require any updates unless the pyvimomi API changes,
    # which is unlikely for the managed objects it is using.
    # PLEASE TEST EXTENSIVELY IF EVER MODIFIED.
    def _upload_image(self, service_instance, host, port,
                      boot_image, datastore_name):

        content = service_instance.RetrieveContent()

        boot_image_folder = 'vmedia'

        # Get the list of all datacenters we have available to us
        datacenters_object_view = content.viewManager.CreateContainerView(
            content.rootFolder,
            [vim.Datacenter],
            True)

        # Find the datastore and datacenter we are using
        datacenter = None
        datastore = None
        for dc_obj in datacenters_object_view.view:
            datastores_object_view = content.viewManager.CreateContainerView(
                dc_obj,
                [vim.Datastore],
                True)
            for ds_obj in datastores_object_view.view:
                if ds_obj.info.name == datastore_name:
                    datacenter = dc_obj
                    datastore = ds_obj
        if not datacenter or not datastore:
            raise Exception("Could not find the datastore specified")

        # Clean up the views now that we have what we need
        datastores_object_view.Destroy()
        datacenters_object_view.Destroy()

        # Create the Virtual Media Directory
        try:
            vmedia_directory = "[{0}] {1}".format(
                datastore.info.name, boot_image_folder)
            file_manager = content.fileManager
            file_manager.MakeDirectory(vmedia_directory, datacenter, True)
        except vim.fault.FileAlreadyExists:
            # Directory already exists so do nothing.
            pass

        # Prepare http PUT call
        isoname = os.path.basename(boot_image)
        http_url = "https://{0}:{1}/folder/{2}/{3}".format(
            self._host, self._port, boot_image_folder, isoname)
        params = {"dsName": datastore.info.name, "dcPath": datacenter.name}

        # Get the cookie built from the current session
        client_cookie = service_instance._stub.cookie
        # Break apart the cookie into it's component parts - This is more than
        # is needed, but a good example of how to break apart the cookie
        # anyways. The verbosity makes it clear what is happening.
        cookie_name = client_cookie.split("=", 1)[0]
        cookie_value = client_cookie.split("=", 1)[1].split(";", 1)[0]
        cookie_path = client_cookie.split("=", 1)[1].split(";", 1)[
            1].split(";", 1)[0].lstrip()
        cookie_text = " " + cookie_value + "; $" + cookie_path
        # Make a cookie
        cookie = dict()
        cookie[cookie_name] = cookie_text

        # Get the request headers set up
        headers = {'Content-Type': 'application/octet-stream'}

        # Get the file to upload ready, extra protection by using with against
        # leaving open threads
        with open(boot_image, "rb") as f:
            # Connect and upload the file
            requests.put(http_url, params=params,
                         data=f, headers=headers,
                         cookies=cookie, verify=False)

        hypervisor_boot_image = "[{0}] {1}/{2}".format(
            datastore.info.name, boot_image_folder, isoname)

        return hypervisor_boot_image

    def vmware_boot_dev_to_sushydev(self, bootable_device):
        if isinstance(bootable_device,
                      vim.VirtualMachineBootOptionsBootableEthernetDevice):
            return 'Pxe'
        elif isinstance(bootable_device,
                        vim.VirtualMachineBootOptionsBootableDiskDevice):
            return 'Hdd'
        elif isinstance(bootable_device,
                        vim.VirtualMachineBootOptionsBootableCdromDevice):
            return 'Cd'
        else:
            return 'None'

    def is_bootable_ethernet_dev(self, dev):
        res = isinstance(dev,
                         vim.VirtualMachineBootOptionsBootableEthernetDevice)
        return res

    def is_bootable_disk_dev(self, dev):
        res = isinstance(dev,
                         vim.VirtualMachineBootOptionsBootableDiskDevice)
        return res

    def is_bootable_cd_dev(self, dev):
        res = isinstance(dev,
                         vim.VirtualMachineBootOptionsBootableCdromDevice)
        return res

    def is_bootable_floppy_dev(self, dev):
        res = isinstance(dev,
                         vim.VirtualMachineBootOptionsBootableFloppyDevice)
        return res

    def is_dev_vmxnet3(self, dev):
        res = isinstance(dev, vim.vm.device.VirtualVmxnet3)
        return res

    def is_dev_vdisk(self, dev):
        res = isinstance(dev, vim.vm.device.VirtualDisk)
        return res

    def is_dev_vcd(self, dev):
        res = isinstance(dev, vim.vm.device.VirtualCdrom)
        return res

    def is_dev_flp(self, dev):
        res = isinstance(dev, vim.vm.device.VirtualFloppy)
        return res

    def is_dev_scsi_cntl(self, dev):
        res = isinstance(dev, vim.vm.device.VirtualSCSIController)
        return res

    def is_dev_sata_cntl(self, dev):
        res = isinstance(dev, vim.vm.device.VirtualSATAController)
        return res

    def is_dev_nvme_cntl(self, dev):
        res = isinstance(dev, vim.vm.device.VirtualNVMEController)
        return res

    def is_iso_backing(self, backing):
        res = isinstance(backing, vim.vm.device.VirtualCdrom.IsoBackingInfo)
        return res

    def reorder_boot_devs(self, boot_source, vm):

        new_boot_order = []
        # Bootable devices exist.
        # Check if the boot_source exists in the list and make it first
        # in the sequence
        if (boot_source == 'Pxe'):
            bootable_eth_dev_found = False
            virtual_eth_dev_found = False
            # Find the device and put it first in the list
            for bootable_dev in vm.config.bootOptions.bootOrder:
                if self.is_bootable_ethernet_dev(bootable_dev):
                    # Found it. Moved it first in the boot list
                    new_boot_order.append(bootable_dev)
                    bootable_eth_dev_found = True

            if not bootable_eth_dev_found:
                # boot_source device was not found in the bootOrder so
                # we need to find the device in the virtual device list
                # and create a bootable device linking to it
                for device in vm.config.hardware.device:
                    if self.is_dev_vmxnet3(device):
                        net_device = \
                            vim.\
                            VirtualMachineBootOptionsBootableEthernetDevice()
                        net_device.deviceKey = device.key
                        # Add to the VM Boot Order
                        new_boot_order.append(net_device)
                        virtual_eth_dev_found = True

                # boot_source does not exist in the virtual device list
                # so raise an exception
                if not virtual_eth_dev_found:
                    vm_name = vm.summary.config.name
                    error_msg = ErrMsg.NO_VIRT_DEV_TO_SUPPORT_BOOT_SRC.format(
                        vm_name, boot_source)
                    raise error.FishyError(error_msg)

            # Add the remaining boot devices from the boot order
            # ommiting the boot_source device
            for bootable_dev in vm.config.bootOptions.bootOrder:
                if not self.is_bootable_ethernet_dev(bootable_dev):
                    new_boot_order.append(bootable_dev)

        elif (boot_source == 'Hdd'):
            bootable_hdd_device_found = False
            virtual_hdd_device_found = False
            # Find the device and put it first in the list
            for bootable_dev in vm.config.bootOptions.bootOrder:
                if self.is_bootable_disk_dev(bootable_dev):
                    # Found it. Moved it first in the boot list
                    new_boot_order.append(bootable_dev)
                    bootable_hdd_device_found = True

            if not bootable_hdd_device_found:
                # boot_source device was not found in the bootOrder so
                # we need to find the device in the virtual device list
                # and create a bootable device linking to it
                for device in vm.config.hardware.device:
                    if self.is_dev_vdisk(device):
                        hdd_device = \
                            vim.VirtualMachineBootOptionsBootableDiskDevice()
                        hdd_device.deviceKey = device.key
                        # Add to the VM Boot Order
                        new_boot_order.append(hdd_device)
                        virtual_hdd_device_found = True

                # boot_source does not exist in the virtual device list
                # so raise an exception
                if not virtual_hdd_device_found:
                    vm_name = vm.summary.config.name
                    error_msg = ErrMsg.NO_VIRT_DEV_TO_SUPPORT_BOOT_SRC.format(
                        vm_name, boot_source)
                    raise error.FishyError(error_msg)

            # Add the remaining boot devices from the boot order
            # ommiting the boot_source device
            for bootable_dev in vm.config.bootOptions.bootOrder:
                if not self.is_bootable_disk_dev(bootable_dev):
                    new_boot_order.append(bootable_dev)
        elif (boot_source == 'Cd'):
            bootable_cd_device_found = False
            virtual_cd_device_found = False
            # Find the device and put it first in the list
            for bootable_dev in vm.config.bootOptions.bootOrder:
                if self.is_bootable_cd_dev(bootable_dev):
                    # Found it. Moved it first in the boot list
                    new_boot_order.append(bootable_dev)
                    bootable_cd_device_found = True

            if not bootable_cd_device_found:
                # boot_source device was not found in the bootOrder so
                # we need to find the device in the virtual device list
                # and create a bootable device linking to it
                for device in vm.config.hardware.device:
                    if self.is_dev_vcd(device):
                        cd_device = \
                            vim.VirtualMachineBootOptionsBootableCdromDevice()
                        # Add to the VM Boot Order
                        new_boot_order.append(cd_device)
                        virtual_cd_device_found = True

                # boot_source does not exist in the virtual device list
                # so raise an exception
                if not virtual_cd_device_found:
                    vm_name = vm.summary.config.name
                    error_msg = ErrMsg.NO_VIRT_DEV_TO_SUPPORT_BOOT_SRC.format(
                        vm_name, boot_source)
                    raise error.FishyError(error_msg)

            # Add the remaining boot devices from the boot order
            # ommiting the boot_source device
            for bootable_dev in vm.config.bootOptions.bootOrder:
                if not self.is_bootable_cd_dev(bootable_dev):
                    new_boot_order.append(bootable_dev)

        return new_boot_order

    def create_boot_order(self, boot_source, vm):

        new_boot_order = []
        # No existing boot order. This is common for a new virtual
        # machine in vmware. The vm.config.bootOptions.bootOrder is
        # empty by default.

        # VMware Documentation Start
        # VirtualMachineBootOptions(vim.vm.BootOptions) - bootOrder
        # Boot order. Listed devices are used for booting. After list
        # is exhausted, default BIOS boot device algorithm is used
        # for booting.
        # Note that order of the entries in the list is important:
        # device listed first is used for boot first, if that one fails
        #  second entry is used, and so on.
        # Platform may have some internal limit on the number of
        # devices it supports. If bootable device is not reached before
        # platform's limit is hit, boot will fail.
        # At least single entry is supported by all products supporting
        # boot order settings.
        # VMware Documentation End.

        # We need to create a new boot order with only one device as
        # there is no previous

        # Check if boot_source exists in the Virtual Devices. There are
        # no vim.vm.BootOptions.BootableDevice in
        # vm.config.bootOptions.bootOrder so
        # we need to find the boot device in the Virtual Hardware
        # device list and create a BootableDevice based on its
        # settings. Usually the device key only.
        if (boot_source == 'Pxe'):
            for device in vm.config.hardware.device:
                if self.is_dev_vmxnet3(device):
                    net_device = \
                        vim.VirtualMachineBootOptionsBootableEthernetDevice()
                    net_device.deviceKey = device.key
                    # Add to the VM Boot Order
                    new_boot_order.append(net_device)

        elif (boot_source == 'Hdd'):
            for device in vm.config.hardware.device:
                if self.is_dev_vdisk(device):
                    hdd_device = \
                        vim.VirtualMachineBootOptionsBootableDiskDevice()
                    hdd_device.deviceKey = device.key
                    # Add to the VM Boot Order
                    new_boot_order.append(hdd_device)

        elif (boot_source == 'Cd'):
            for device in vm.config.hardware.device:
                if self.is_dev_vcd(device):
                    cd_device = \
                        vim.VirtualMachineBootOptionsBootableCdromDevice()
                    # Add to the VM Boot Order
                    new_boot_order.append(cd_device)

        return new_boot_order

    @classmethod
    def initialize(cls, config, logger, host, port, username, password,
                   vmware_vmedia_datastore, *args, **kwargs):
        """Initialize class attribute."""
        cls._config = config
        cls._logger = logger

        cls._host = host
        cls._port = port
        cls._username = username
        cls._password = password
        cls._vmware_vmedia_datastore = vmware_vmedia_datastore

        return cls

    @property
    def driver(self):
        """Return human-friendly driver information

        :returns: driver information as `str`
        """
        return '<vmware>'

    @property
    def systems(self):
        """Return available computer systems

        :returns: list of UUIDs representing the systems
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:

            vmlist = self._get_vms(service_instance)
            # return [vm.summary.config.uuid for vm in vmlist]
            return [vm.summary.config.name for vm in vmlist]

    def uuid(self, identity):
        """Get computer system UUID

        The universal unique identifier (UUID) for this system. Can be used
        in place of system name if there are duplicates.

        If virtualization backend does not support non-unique system identity,
        this method may just return the `identity`.

        :returns: computer system UUID
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)
            return vm.summary.config.uuid

    def name(self, identity):
        """Get computer system name by UUID

        The universal unique identifier (UUID) for this system. Can be used
        in place of system name if there are duplicates.

        If virtualization backend does not support system names
        this method may just return the `identity`.

        :returns: computer system name
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)
            return vm.summary.config.name

    def get_power_state(self, identity):
        """Get computer system power state

        :returns: current power state as *On* or *Off* `str` or `None`
            if power state can't be determined
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            if vm.summary.runtime.powerState == 'poweredOn':
                return 'On'
            elif vm.summary.runtime.powerState == 'poweredOff':
                return 'Off'
            # Not sure how to implement "can't be determined". I assume check
            # for None(?) vim.VirtualMachine.PowerState is an enum with 3
            # values. poweredOff, poweredOn, suspended
            elif vm.summary.runtime.powerState is None:
                return 'None'

    def set_power_state(self, identity, state):
        """Set computer system power state

        :param state: string literal requesting power state transition.
            Valid values  are: *On*, *ForceOn*, *ForceOff*, *GracefulShutdown*,
            *GracefulRestart*, *ForceRestart*, *Nmi*.

        :raises: `FishyError` if power state can't be set
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)
            try:
                if state == PowerStates.ON.value:
                    vm.PowerOn()
                elif state == PowerStates.FORCE_ON.value:
                    vm.PowerOn()
                elif state == PowerStates.FORCE_OFF.value:
                    vm.PowerOff()
                elif state == PowerStates.GRACEFUL_SHUTDOWN.value:
                    vm.ShutdownGuest()
                elif state == PowerStates.GRACEFUL_RESTART.value:
                    vm.RebootGuest()
                elif state == PowerStates.FORCE_RESTART.value:
                    vm.ResetVM_Task()
                elif state == PowerStates.NMI.value:
                    vm.SendNMI()
            except Exception:
                raise error.FishyError(
                    ErrMsg.POWER_STATE_CANNOT_SET.format(state, identity))

    def get_boot_device(self, identity):
        """Get computer system boot device name

        :returns: boot device name as `str` or `None` if device name
            can't be determined
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            num_of_boot_devices = len(vm.config.bootOptions.bootOrder)

            if (num_of_boot_devices > 0):
                first_boot_dev = vm.config.bootOptions.bootOrder[0]
                return self.vmware_boot_dev_to_sushydev(first_boot_dev)
            else:
                return 'None'

    def set_boot_device(self, identity, boot_source):
        """Set computer system boot device name

        :param boot_source: string literal requesting boot device change on the
            system. Valid values are: *Pxe*, *Hdd*, *Cd*.

        :raises: `FishyError` if boot device can't be set
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            # Check for a valid boot source. only Pxe. Hdd, Cd are supported
            if boot_source not in ['Pxe', 'Hdd', 'Cd']:
                error_msg = ErrMsg.INVALID_BOOT_SOURCE.format(boot_source)
                raise error.FishyError(error_msg)

            # Initialize array for the new boot order
            new_boot_order = []

            # Get existing boot devices
            num_of_boot_devs = len(vm.config.bootOptions.bootOrder)
            # Check if there are existing boot devices.
            if (num_of_boot_devs > 0):
                # Bootable devices exist.
                # Check if the boot_source exists in the list and make it first
                # in the sequence
                new_boot_order = self.reorder_boot_devs(boot_source, vm)
            else:
                # No existing boot order. This is common for a new virtual
                # machine in vmware. The vm.config.bootOptions.bootOrder is
                # empty by default.
                new_boot_order = self.create_boot_order(boot_source, vm)

            # Create configSpec object with the new boot order
            new_vm_config_spec = vim.VirtualMachineConfigSpec()
            new_vm_boot_options = vim.VirtualMachineBootOptions()
            new_vm_boot_options.bootOrder = new_boot_order
            new_vm_config_spec.bootOptions = new_vm_boot_options

            # Reconfig VM
            vm.ReconfigVM_Task(new_vm_config_spec)

    def get_boot_mode(self, identity):
        """Get computer system boot mode.

        :returns: either *UEFI* or *Legacy* as `str` or `None` if
            current boot mode can't be determined
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            firmware = vm.config.firmware

            if firmware == vim.GuestOsDescriptorFirmwareType.bios:
                return 'Legacy'
            elif firmware == vim.GuestOsDescriptorFirmwareType.efi:
                return 'UEFI'
            else:
                return 'None'

    def set_boot_mode(self, identity, boot_mode):
        """Set computer system boot mode.

        :param boot_mode: string literal requesting boot mode
            change on the system. Valid values are: *UEFI*, *Legacy*.

        :raises: `FishyError` if boot mode can't be set
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            if boot_mode not in ['UEFI', 'Legacy']:
                error_msg = ErrMsg.INVALID_BOOT_MODE
                raise error.FishyError(error_msg)

            new_boot_mode = None

            if boot_mode == 'Legacy':
                new_boot_mode = vim.GuestOsDescriptorFirmwareType.bios
            elif boot_mode == 'UEFI':
                new_boot_mode = vim.GuestOsDescriptorFirmwareType.efi

            # Create configSpec object
            new_vm_config_spec = vim.VirtualMachineConfigSpec()
            new_vm_config_spec.firmware = new_boot_mode

            # Reconfig VM
            vm.ReconfigVM_Task(new_vm_config_spec)

    def get_total_memory(self, identity):
        """Get computer system total memory

        :returns: available RAM in GiB as `int` or `None` if total memory
            count can't be determined
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            mem_in_mb = vm.config.hardware.memoryMB

            if mem_in_mb is not None:
                mem_in_gib = mem_in_mb / 1024
                return mem_in_gib
            else:
                return 'None'

    def get_total_cpus(self, identity):
        """Get computer system total count of available CPUs

        :returns: available CPU count as `int` or `None` if CPU count
            can't be determined
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            numCPU = vm.config.hardware.numCPU

            if numCPU is not None:
                return numCPU
            else:
                return 'None'

    def get_bios(self, identity):
        """Get BIOS attributes for the system

        :returns: key-value pairs of BIOS attributes

        :raises: `FishyError` if BIOS attributes cannot be processed
        """
        # VMware API does not support getting the BIOS Settings
        error_msg = ErrMsg.DRV_OP_NOT_SUPPORTED.format("BIOS Settings")
        raise error.NotSupportedError(error_msg)

    def set_bios(self, identity, attributes):
        """Update BIOS attributes

        :param attributes: key-value pairs of attributes to update

        :raises: `FishyError` if BIOS attributes cannot be processed
        """
        # VMware API does not support setting the BIOS Settings
        error_msg = ErrMsg.DRV_OP_NOT_SUPPORTED.format("BIOS Settings")
        raise error.NotSupportedError(error_msg)

    def reset_bios(self, identity):
        """Reset BIOS attributes to default

        :raises: `FishyError` if BIOS attributes cannot be processed
        """
        # VMware API does not support resetting the BIOS Settings
        error_msg = ErrMsg.DRV_OP_NOT_SUPPORTED.format("BIOS Settings")
        raise error.NotSupportedError(error_msg)

    def get_nics(self, identity):
        """Get list of NICs and their attributes

        :returns: list of dictionaries of NICs and their attributes
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            net_devices = []

            for device in vm.config.hardware.device:
                if isinstance(device, vim.vm.device.VirtualEthernetCard):
                    net_devices.append(
                        {'id': device.macAddress, 'mac': device.macAddress})

            return net_devices

    def get_boot_image(self, identity, device):
        """Get backend VM boot image info

        :param identity: node name or ID
        :param device: device type (from
            `sushy_tools.emulator.constants`)
        :returns: a `tuple` of (boot_image, write_protected, inserted)
        :raises: `error.FishyError` if boot device can't be accessed
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            # Check for valid device type based on
            # sushy_tools.emulator.constants
            if device not in [constants.DEVICE_TYPE_CD,
                              constants.DEVICE_TYPE_FLOPPY,
                              constants.DEVICE_TYPE_HDD,
                              constants.DEVICE_TYPE_PXE]:
                error_msg = ErrMsg.INVALID_DEVICE_TYPE.format(device)
                raise error.FishyError(error_msg)

            boot_image = ""
            write_protected = False
            inserted = False

            # Get number of boot devices
            num_of_boot_devs = len(vm.config.bootOptions.bootOrder)

            # Get boot device or fail if no boot devices
            if (num_of_boot_devs > 0):
                # Find the bootable device in the Virtual Devices, Get the
                # backing filename, set write_protected and inserted
                for bootbl_dev in vm.config.bootOptions.bootOrder:
                    if device == constants.DEVICE_TYPE_PXE:
                        if self.is_bootable_ethernet_dev(bootbl_dev):
                            for vdev in vm.config.hardware.device:
                                if (self.is_dev_vmxnet3(vdev)
                                        and vdev.key == bootbl_dev.deviceKey):
                                    boot_image = ""
                                    write_protected = False
                                    inserted = False
                                    # Found the device, exit the loop
                                    break
                    elif device == constants.DEVICE_TYPE_HDD:
                        if self.is_bootable_disk_dev(bootbl_dev):
                            for vdev in vm.config.hardware.device:
                                if (self.is_dev_vdisk(vdev)
                                        and vdev.key == bootbl_dev.deviceKey):
                                    boot_image = vdev.backing.fileName
                                    write_protected = False
                                    inserted = False
                                    # Found the device, exit the loop
                                    break
                    elif device == constants.DEVICE_TYPE_CD:
                        if self.is_bootable_cd_dev(bootbl_dev):
                            for vdev in vm.config.hardware.device:
                                if self.is_dev_vcd(vdev):
                                    boot_image = vdev.backing.fileName
                                    write_protected = True
                                    inserted = True
                                    # Found the device, exit the loop
                                    break
                    elif device == constants.DEVICE_TYPE_FLOPPY:
                        if self.is_bootable_floppy_dev(bootbl_dev):
                            for vdev in vm.config.hardware.device:
                                if self.is_dev_flp(vdev):
                                    boot_image = vdev.backing.fileName
                                    write_protected = True
                                    inserted = True
                                    # Found the device, exit the loop
                                    break

                # Note. If nothing has matched after the exit of the for loop
                # then the empty default values will be returned.
            else:
                error_msg = ErrMsg.NO_BOOTABLE_DEVICE
                raise error.FishyError(error_msg)

            # Return tuple with boot_image, write_protected, inserted
            return boot_image, write_protected, inserted

    def set_boot_image(self, identity, device, boot_image=None,
                       write_protected=True):
        """Set backend VM boot image

        :param identity: node name or ID
        :param device: device type (from
            `sushy_tools.emulator.constants`)
        :param boot_image: path to the image file or `None` to remove
            configured image entirely
        :param write_protected: expose media as read-only or writable

        :raises: `error.FishyError` if boot device can't be set
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            # Check for valid device type based on
            # sushy_tools.emulator.constants. We support only Cd and Floppy at
            # this point. VirtualMedia CD/DVD/Floppy/USBDisk like
            # functionality.
            if device not in [constants.DEVICE_TYPE_CD,
                              constants.DEVICE_TYPE_FLOPPY]:
                error_msg = ErrMsg.INVALID_DEVICE_TYPE.format(device)
                raise error.FishyError(error_msg)

            trg_dev = None
            trg_dev_edit = False
            trg_dev_remove = False
            trg_dev_add = False

            # EjectMedia
            # Check if no boot image is provided
            if boot_image is None:
                for vdev in vm.config.hardware.device:
                    if device == constants.DEVICE_TYPE_CD:
                        if self.is_dev_vcd(vdev):
                            # Cdrom device found so we need to remove the iso.
                            # Switch the
                            # vim.vm.device.VirtualDevice.BackingInfo of the
                            # device to AtapiBackingInfo()
                            # Note. We could remove the device completely but
                            # it might be part of the vm spec so let's leave
                            # this here for the time being.
                            trg_dev = vdev
                            trg_dev.backing = \
                                vim.vm.device.VirtualCdrom.AtapiBackingInfo()
                            trg_dev.connectable.connected = True
                            trg_dev.connectable.startConnected = True
                            trg_dev.backing.useAutoDetect = True
                            trg_dev_edit = True
                    elif device == constants.DEVICE_TYPE_FLOPPY:
                        if isinstance(vdev, vim.vm.device.VirtualFloppy):
                            # Floppy Disk. vSphere does not support empty
                            # floppy drives or floppy drives mapped to a
                            # physical device on the host.
                            # Search for "Change the Floppy Drive
                            # Configuration in the vSphere Web Client" in
                            # vmware docs for more information
                            # As a workaround we have to remove the device
                            # completely and add it on InsertMedia.
                            trg_dev = vdev
                            trg_dev_remove = True
            else:

                # InsertMedia
                # Upload the image and return its path in the hypervisor.
                # It shoud be similar to [datastore_name]
                # some_folder/image_file_name.img .iso .raw etc
                esxi_boot_image_path = self._upload_image(
                    service_instance, self._host, self._port,
                    boot_image, self._vmware_vmedia_datastore)
                device_found = False

                for vdev in vm.config.hardware.device:
                    if device == constants.DEVICE_TYPE_CD:
                        if isinstance(vdev, vim.vm.device.VirtualCdrom):
                            device_found = True
                            # Device Found
                            # If the BackingInfo() is ISO then update the
                            # backing.filename with the boot image path in the
                            # hypervisor
                            if self.is_iso_backing(vdev.backing):
                                trg_dev = vdev
                                trg_dev.connectable.connected = True
                                trg_dev.connectable.startConnected = True
                                trg_dev.backing.fileName = esxi_boot_image_path
                            else:
                                # Switch the backing info to ISO
                                trg_dev = vdev
                                trg_dev.backing = \
                                    vim.vm.device.VirtualCdrom.IsoBackingInfo()
                                trg_dev.connectable.connected = True
                                trg_dev.connectable.startConnected = True
                                trg_dev.backing.fileName = esxi_boot_image_path
                            trg_dev_edit = True
                    elif device == constants.DEVICE_TYPE_FLOPPY:
                        if self.is_dev_flp(vdev):
                            # Device Found
                            device_found = True
                            trg_dev = vdev
                            trg_dev.backing.fileName = esxi_boot_image_path
                            trg_dev_edit = True

                if not device_found:
                    # Device was not found so add the device and set the
                    # backing filename
                    if device == constants.DEVICE_TYPE_CD:
                        trg_dev = vim.vm.device.VirtualCdrom()
                        trg_dev.backing = \
                            vim.vm.device.VirtualCdrom.IsoBackingInfo()
                        trg_dev.connectable = \
                            vim.vm.device.VirtualDevice.ConnectInfo()
                        trg_dev.connectable.connected = True
                        trg_dev.connectable.startConnected = True
                        trg_dev.backing.fileName = esxi_boot_image_path
                        trg_dev_add = True
                    elif device == constants.DEVICE_TYPE_FLOPPY:
                        trg_dev = vim.vm.device.VirtualFloppy()
                        target_device_backing = \
                            vim.vm.device.VirtualFloppy.ImageBackingInfo()
                        target_device_backing.fileName = esxi_boot_image_path
                        trg_dev.backing = target_device_backing
                        trg_dev_add = True

            # NOTE. No need to set the boot device here!!! This is a separate
            # step from the user. Mount the disk and then set the virtual
            # media device manually to boot.
            # See
            # https://docs.openstack.org/sushy-tools/
            # latest/user/dynamic-emulator.html#virtual-media-boot

            try:
                # Update the Virtual Machine Configuration with the
                # updated device. We changed the backing of the device so we
                # need to update the configuration.

                # Check that we have found a device to change. If it is None
                # here then it is propably an EjectMedia for a vm with a non
                # existing Cd or Floppy.
                # Not sure if we need to create a custom message for this in
                # the ErrMsg. The exception will be caught in
                # this block and
                # ErrMsg.BOOT_IMAGE_CANNOT_BE_SET will
                # be raised.
                if trg_dev is None:
                    raise Exception("No such device present.")

                # Create a Virtual Machine Config Specification
                updated_vm_config_spec = vim.VirtualMachineConfigSpec()

                # Create a VirtualDeviceConfigSpec and assign the target device
                # to it. Also set its operation to edit.
                updated_device_config_spec = vim.VirtualDeviceConfigSpec()
                updated_device_config_spec.device = trg_dev

                if trg_dev_add:
                    updated_device_config_spec.operation = \
                        vim.VirtualDeviceConfigSpecOperation().add
                elif trg_dev_edit:
                    updated_device_config_spec.operation = \
                        vim.VirtualDeviceConfigSpecOperation().edit
                elif trg_dev_remove:
                    updated_device_config_spec.operation = \
                        vim.VirtualDeviceConfigSpecOperation().remove

                # Create a list with the device config specs.
                device_updates = []
                device_updates.append(updated_device_config_spec)

                # Set the list with the updated devices in the Virtual Machine
                # Config Specification
                updated_vm_config_spec.deviceChange = device_updates

                # Reconfig VM
                vm.ReconfigVM_Task(updated_vm_config_spec)

            except Exception:
                error_msg = ErrMsg.BOOT_IMAGE_CANNOT_BE_SET.format(
                    boot_image, device)
                raise error.FishyError(error_msg)

    def get_simple_storage_collection(self, identity):
        """Get a dict of Simple Storage Controllers and their devices

        :returns: dict of Simple Storage Controllers and their atributes
        """
        with VmwareOpen(self._host, self._port,
                        self._username, self._password) as service_instance:
            vm = self._get_vm(identity, service_instance)

            s_storage = defaultdict(
                lambda: defaultdict(DeviceList=list()))

            supported_cntl_devs = []

            # Get the supported controller devices from the hardware list
            for vdev in vm.config.hardware.device:
                # We support SCSI, NVME and SATA Controllers. Full list of
                # Virtual VMWare Controllers can be found at
                # https://code.vmware.com/apis/968
                if (self.is_dev_scsi_cntl(vdev)
                        or self.is_dev_nvme_cntl(vdev)
                        or self.is_dev_sata_cntl(vdev)):

                    supported_cntl_devs.append(vdev)

            # Loop the supported controllers and get their devices
            for cntl_dev in supported_cntl_devs:

                # Fix the controller label. Replace space with hyphen
                cntl_type = cntl_dev.deviceInfo.label.replace(
                    " ", "-")

                # Get keys for the devices currently controlled by
                # this controller.
                # vim.vm.device.VirtualController
                # List of devices currently controlled by this controller.
                # Each entry contains the key property of the corresponding
                # vdev object.
                cntl_devs_keys = cntl_dev.device

                # Get the devices currently controlled by this controller.
                for key in cntl_devs_keys:
                    for vdev in vm.config.hardware.device:
                        # Handle only VirtualDisks. Other devices attached to
                        # the controllers suchs a VirtualFloppy and
                        # VirtualCdrom MUST be excluded.
                        if self.is_dev_vdisk(vdev):
                            # Get only the VirtualDisk attached to the
                            # Controller.
                            if (vdev.key == key):
                                disk_device = {
                                    'Name': vdev.backing.fileName,
                                    'CapacityBytes': vdev.capacityInBytes}

                                s_storage[cntl_type]['Id'] = cntl_type
                                s_storage[cntl_type]['Name'] = cntl_type
                                s_storage[cntl_type]['DeviceList'].append(
                                    disk_device)

            return s_storage

    def find_or_create_storage_volume(self, data):
        """Find/create volume based on existence in the virtualization backend

        :param data: data about the volume in dict form with values for `Id`,
                     `Name`, `CapacityBytes`, `VolumeType`, `libvirtPoolName`
                     and `libvirtVolName`

        :returns: Id of the volume if successfully found/created else None
        """
        # Need to review the implementation for this.
        # The data params are libvirt related rather than hypervisor generic.
        # I assume the following:
        # VolumeType = VMDK?
        # libvirtPoolName = Datastore Name
        # libvirtVolName = VMDK filepath
        raise error.NotSupportedError('Not implemented')
