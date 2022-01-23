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

from enum import Enum
from unittest import mock

from oslotest import base
from pyVmomi import vim
from pyVmomi import VmomiSupport

from sushy_tools.emulator.resources.systems.vmwaredriver \
    import ErrMsg
from sushy_tools.emulator.resources.systems.vmwaredriver \
    import PowerStates
from sushy_tools.emulator.resources.systems.vmwaredriver \
    import VmwareDriver
from sushy_tools import error


class FakeVDevice(object):
    def __init__(self):
        pass


class FakeVBootableDevice(FakeVDevice):
    def __init__(self, bootable, boot_order):
        # Bootable Device.
        self.bootable = bootable
        # Boot Order. Start from 1. Set this to zero 0 if bootable is False.
        self.boot_order = boot_order


class FakeVEthernetDevice(FakeVBootableDevice):
    def __init__(self, key, mac, bootable, boot_order):
        super().__init__(bootable, boot_order)
        self.key = key
        self.mac = mac


class FakeVDiskDevice(FakeVBootableDevice):
    def __init__(self, key, bootable, boot_order,
                 backing_file, capacity_in_bytes):
        super().__init__(bootable, boot_order)
        self.key = key
        self.backing_file = backing_file
        self.capacity_in_bytes = capacity_in_bytes


class FakeVCdromDevice(FakeVBootableDevice):
    def __init__(self, bootable, boot_order, backing_iso):
        super().__init__(bootable, boot_order)
        self.backing_iso = backing_iso


class FakeVFloppyDevice(FakeVBootableDevice):
    def __init__(self, key, bootable, boot_order, backing_file):
        super().__init__(bootable, boot_order)
        self.key = key
        self.backing_file = backing_file


class FakeVControllerDevice(FakeVDevice):
    def __init__(self, key, label, device_id_list):
        self.key = key
        self.label = label
        self.device_id_list = device_id_list


class FakeVSATAControllerDevice(FakeVControllerDevice):
    def __init__(self, key, label, device_id_list):
        super().__init__(key, label, device_id_list)


class FakeVSCSIControllerDevice(FakeVControllerDevice):
    def __init__(self, key, label, device_id_list):
        super().__init__(key, label, device_id_list)


class FakeVNVMEControllerDevice(FakeVControllerDevice):
    def __init__(self, key, label, device_id_list):
        super().__init__(key, label, device_id_list)


class FakeFirmware(Enum):
    BIOS = 1
    UEFI = 2
    # NONE is not really supported by vmware. All new virtual machines get
    # the defaultvalue of BIOS/Legacy. Adding it just to meet the testing
    # requirements of get_boot_mode to return None if firmware cannot
    # be determined.
    NONE = 3


class FakeVMWareVM(object):
    def __init__(self, moid, name, uuid, is_powered, firmware,
                 memory_mb, num_cpu, devices):
        self.moid = moid
        self.name = name
        self.uuid = uuid
        self.is_powered = is_powered
        self.firmware = firmware
        self.memory_mb = memory_mb
        self.num_cpu = num_cpu
        self.devices = devices


class FakeException(Enum):
    VMWARE_OPEN_MOCK_EXCEPTION = \
        "Mock Exception on vim.ServiceInstance.RetrieveContent()"
    VMWARE_CONN_MOCK_EXCEPTION = "Mock Connection Error"


class ExceptionMsg(Enum):
    INV_BOOT_DEV = ("Invalid bootable device. See Subclasses of "
                    "FakeVDevice for available options.")
    DSK_NO_BACKING = ("Virtual Disk with no backing file? Is this valid?"
                      "Please review the test data / implementation.")
    INV_VIRT_DEV = ("Invalid Virtual Device. See Subclasses of FakeVDevice"
                    " for available options.")
    INV_FIRM_TYPE = ("Invalid firmware type {0}."
                     " Only bios and uefi are valid values.")


class VMWareDriverTestCase(base.BaseTestCase):

    # Sets up a service instance
    def setup_service_instance(self, fake_vmware_vm_array,
                               raises_retrieve_content_exception):

        # Initialize VM List
        vm_folder_children = []

        for fake_vm in fake_vmware_vm_array:
            # Initialize the Bootable Devices.
            bootable_devs = []
            virtual_devices = []

            # Get the bootable devices
            bootable_devices = []

            for device in fake_vm.devices:
                if (isinstance(device, FakeVBootableDevice)
                        and device.bootable):
                    bootable_devices.append(device)

            # Sort the bootable devices by boot order
            sorted_bootable_devices = sorted(bootable_devices,
                                             key=lambda fake_virtual_device:
                                             fake_virtual_device.boot_order)

            for bootable_device in sorted_bootable_devices:
                if isinstance(bootable_device, FakeVCdromDevice):
                    bootable_cdrom_dev = mock.MagicMock(
                        spec_set=vim.vm.BootOptions.BootableCdromDevice())
                    bootable_devs.append(bootable_cdrom_dev)
                elif isinstance(bootable_device, FakeVDiskDevice):
                    bootable_disk_dev = mock.MagicMock(
                        spec_set=vim.vm.BootOptions.BootableDiskDevice())
                    bootable_disk_dev.deviceKey = bootable_device.key
                    bootable_devs.append(bootable_disk_dev)
                elif isinstance(bootable_device, FakeVEthernetDevice):
                    bootable_eth_dev = mock.MagicMock(
                        spec_set=vim.vm.BootOptions.BootableEthernetDevice())
                    bootable_eth_dev.deviceKey = bootable_device.key
                    bootable_devs.append(bootable_eth_dev)
                else:
                    error_msg = ExceptionMsg.INV_BOOT_DEV
                    raise Exception(error_msg)

            # Initialize the Virtual Devices
            for device in fake_vm.devices:
                if isinstance(device, FakeVCdromDevice):
                    cd_dev = mock.MagicMock(
                        spec_set=vim.vm.device.VirtualCdrom())

                    if device.backing_iso is not None:

                        cd_dev_iso_backing = \
                            mock.MagicMock(spec_set=vim.vm.device.
                                           VirtualCdrom.IsoBackingInfo())
                        cd_dev_iso_backing.fileName = device.backing_iso
                        cd_dev.backing = cd_dev_iso_backing
                    else:
                        cd_dev_atapi_back = mock.MagicMock(
                            spec_set=vim.vm.device.VirtualCdrom.
                            AtapiBackingInfo())
                        cd_dev_atapi_back.useAutoDetect = True
                        cd_dev.backing = cd_dev_atapi_back

                    virtual_devices.append(cd_dev)
                elif isinstance(device, FakeVDiskDevice):
                    hdd_dev = mock.MagicMock(
                        spec_set=vim.vm.device.VirtualDisk())
                    hdd_dev.key = device.key
                    hdd_dev.capacityInBytes = device.capacity_in_bytes

                    if device.backing_file is not None:
                        hdd_dev_file_backing = mock.MagicMock(
                            spec_set=vim.vm.device.VirtualDevice.
                            FileBackingInfo())
                        hdd_dev_file_backing.fileName = device.backing_file
                        hdd_dev.backing = hdd_dev_file_backing
                    else:
                        error_msg = ExceptionMsg.DSK_NO_BACKING
                        raise Exception(error_msg)

                    virtual_devices.append(hdd_dev)
                elif isinstance(device, FakeVEthernetDevice):
                    net_dev = mock.MagicMock(spec_set=vim.vm.device.
                                             VirtualVmxnet3())
                    net_dev.key = device.key
                    net_dev.macAddress = device.mac
                    virtual_devices.append(net_dev)
                elif isinstance(device, FakeVFloppyDevice):
                    floppy_dev = mock.MagicMock(spec_set=vim.vm.device.
                                                VirtualFloppy())
                    floppy_dev_image_back = mock.MagicMock(
                        spec_set=vim.vm.device.VirtualFloppy.
                        ImageBackingInfo())
                    floppy_dev_image_back.fileName = device.backing_file
                    floppy_dev.key = device.key
                    floppy_dev.backing = floppy_dev_image_back
                    virtual_devices.append(floppy_dev)
                elif isinstance(device, FakeVSATAControllerDevice):
                    controller_dev = mock.MagicMock(
                        spec_set=vim.vm.device.VirtualSATAController())
                    controller_dev.device = device.device_id_list
                    controller_dev.key = device.key
                    test_vm_00_device_info = mock.MagicMock(
                        spec_set=vim.Description())
                    test_vm_00_device_info.label = device.label
                    controller_dev.deviceInfo = test_vm_00_device_info
                    virtual_devices.append(controller_dev)
                elif isinstance(device, FakeVSCSIControllerDevice):
                    controller_dev = mock.MagicMock(
                        spec_set=vim.vm.device.VirtualSCSIController())
                    controller_dev.device = device.device_id_list
                    controller_dev.key = device.key
                    test_vm_00_device_info = mock.MagicMock(
                        spec_set=vim.Description())
                    test_vm_00_device_info.label = device.label
                    controller_dev.deviceInfo = test_vm_00_device_info
                    virtual_devices.append(controller_dev)
                elif isinstance(device, FakeVNVMEControllerDevice):
                    controller_dev = mock.MagicMock(
                        spec_set=vim.vm.device.VirtualNVMEController())
                    controller_dev.device = device.device_id_list
                    controller_dev.key = device.key
                    test_vm_00_device_info = mock.MagicMock(
                        spec_set=vim.Description())
                    test_vm_00_device_info.label = device.label
                    controller_dev.deviceInfo = test_vm_00_device_info
                    virtual_devices.append(controller_dev)
                else:
                    error_mgs = ExceptionMsg.INV_VIRT_DEV
                    raise Exception(error_mgs)

            # Initialize Firmware
            if fake_vm.firmware == FakeFirmware.BIOS:
                firmware_type = vim.GuestOsDescriptorFirmwareType.bios
            elif fake_vm.firmware == FakeFirmware.UEFI:
                firmware_type = vim.GuestOsDescriptorFirmwareType.efi
            elif fake_vm.firmware == FakeFirmware.NONE:
                firmware_type = None
            else:
                error_msg = ExceptionMsg.INV_FIRM_TYPE.format(fake_vm.firmware)
                raise Exception(error_msg)

            # Initialize Hardware
            virt_hardware = mock.MagicMock(spec_set=vim.vm.VirtualHardware())
            virt_hardware.device = virtual_devices
            virt_hardware.memoryMB = fake_vm.memory_mb
            virt_hardware.numCPU = fake_vm.num_cpu

            # Initialize Boot Options
            boot_opts = mock.MagicMock(spec_set=vim.vm.BootOptions())
            boot_opts.bootOrder = bootable_devs

            # Initialize Config Info
            config_info = mock.MagicMock(spec_set=vim.vm.ConfigInfo())
            config_info.bootOptions = boot_opts
            config_info.hardware = virt_hardware
            config_info.firmware = firmware_type

            # Initialize the Config Summary
            config_summary = mock.MagicMock(
                spec_set=vim.vm.Summary.ConfigSummary())
            config_summary.name = fake_vm.name
            config_summary.uuid = fake_vm.uuid

            # Initialize the Virtual Machine Runtime Info
            runtime_info = mock.MagicMock(spec_set=vim.vm.RuntimeInfo())
            if fake_vm.is_powered:
                runtime_info.powerState = 'poweredOn'
            else:
                runtime_info.powerState = 'poweredOff'

            # Initialize VM Summary
            vm_summary = mock.MagicMock(spec_set=vim.vm.Summary())
            vm_summary.config = config_summary
            vm_summary.runtime = runtime_info

            # Initialize the Mock VM
            test_vm = mock.MagicMock(spec_set=vim.VirtualMachine(fake_vm.moid))
            test_vm.summary = vm_summary
            test_vm.config = config_info

            vm_folder_children.append(test_vm)

        # Initialize the Data Center and add the Mock VM
        # Create the vmFolder
        vm_folder_mock = mock.MagicMock(spec_set=vim.Folder("vm-folder"))
        vm_folder_mock.childType = \
            {"vim.Folder", "vim.Virtualmachine", "vim.VirtualApp"}
        # Add it to the Data Center and add the VM in the children
        ds_mock = mock.MagicMock(spec_set=vim.Datacenter("ds-00"))
        ds_mock.vmFolder = vm_folder_mock
        ds_mock.vmFolder.childEntity = vm_folder_children

        # Initialize the Service Instance Mock and add the Data Center
        # Create the rootFolder
        root_folder_mock = mock.MagicMock(spec_set=vim.Folder("root-folder"))
        root_folder_mock.childEntity = [ds_mock]
        # Create the Service Instance and add the rootFolder
        si_mock = mock.MagicMock(spec_set=vim.ServiceInstance("si-00"))
        si_content_mock = mock.MagicMock(spec_set=vim.ServiceInstanceContent())
        si_content_mock.rootFolder = root_folder_mock

        # Mock View Manager.
        # This kicks when we use the vim.view.ViewManager of the
        # vim.ServiceInstanceContent to get all the vms. It is the easiest way
        # to traverse all the datacenter and vm folder tree.
        # If we use the datacenter.vmFolder for this then we need loop
        # recursively into the folder for subfolders.
        # Subfolders have not been mocked in this Test Suite and was considered
        # a bit of testing overhead.
        # The content.viewManager.CreateContainerView is used instead
        # and it mocked to return all the vms.
        view_manager_mock = mock.MagicMock(
            spec_set=vim.view.ViewManager("vmg-00"))
        container_view_mock = mock.MagicMock(
            spec_set=vim.view.ContainerView("cv-00"))
        container_view_mock.view = vm_folder_children
        view_manager_mock.CreateContainerView = mock.MagicMock(
            return_value=container_view_mock)
        si_content_mock.viewManager = view_manager_mock

        # Mock the RetrieveContent Method in the service instance
        if raises_retrieve_content_exception:
            # Mock an exception
            mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
            si_mock.RetrieveContent.side_effect = Exception(mock_error_message)
        else:
            si_mock.RetrieveContent = mock.MagicMock(
                return_value=si_content_mock)

        return si_mock, vm_folder_children

    def init_data(self):
        # Setup Test Data

        # Initialize Fake Devices

        # VM0
        fake_mac_0_0 = "C9-33-31-09-AB-7E"
        fake_back_file_0_0 = "[datastore01] test00/test_vm_00.vmdk"
        fake_v_eth_dev_0_0 = FakeVEthernetDevice(1000, fake_mac_0_0, True, 1)
        fake_v_disk_0_0 = FakeVDiskDevice(2000, True, 2,
                                          fake_back_file_0_0, 1073741824)
        fake_v_cdrom_0_0 = FakeVCdromDevice(True, 3, None)

        fake_devs_vm_0 = [fake_v_eth_dev_0_0, fake_v_disk_0_0,
                          fake_v_cdrom_0_0]

        # VM1
        fake_mac_1_0 = "62-56-A1-79-0D-BC"
        fake_back_file_1_0 = "[datastore01] test00/test_vm_01.vmdk"
        fake_v_eth_dev_1_0 = FakeVEthernetDevice(1100, fake_mac_1_0, False, 0)
        fake_v_disk_1_0 = FakeVDiskDevice(2100, True, 1,
                                          fake_back_file_1_0, 1073741824)
        fake_v_cdrom_1_0 = FakeVCdromDevice(True, 2, None)

        fake_devs_vm_1 = [fake_v_eth_dev_1_0, fake_v_disk_1_0,
                          fake_v_cdrom_1_0]

        # VM2
        fake_mac_2_0 = "D4-88-AC-71-13-2A"
        fake_back_iso_2_0 = "[datastore01] vmedia/test.iso"
        fake_back_file_2_0 = "[datastore01] test00/test_vm_02.vmdk"
        fake_v_eth_dev_2_0 = FakeVEthernetDevice(1200, fake_mac_2_0, False, 0)
        fake_v_disk_2_0 = FakeVDiskDevice(2200, False, 0,
                                          fake_back_file_2_0, 1073741824)
        fake_v_cdrom_2_0 = FakeVCdromDevice(True, 1, fake_back_iso_2_0)

        fake_devs_vm_2 = [fake_v_eth_dev_2_0, fake_v_disk_2_0,
                          fake_v_cdrom_2_0]

        # VM3
        fake_mac_3_0 = "D4-88-AC-71-13-2A"
        fake_back_file_3_0 = "[datastore01] test00/test_vm_03.vmdk"
        fake_v_eth_dev_3_0 = FakeVEthernetDevice(1300, fake_mac_3_0, True, 1)
        fake_v_disk_3_0 = FakeVDiskDevice(2300, True, 2,
                                          fake_back_file_3_0, 1073741824)

        fake_devs_vm_3 = [fake_v_eth_dev_3_0, fake_v_disk_3_0]

        # VM4
        fake_mac_4_0 = "26-A5-F4-31-89-3E"
        fake_mac_4_1 = "50-54-11-AB-12-CF"
        fake_backing_file_4_0 = "[datastore01] test00/test_vm_04.vmdk"
        fake_v_eth_dev_4_0 = FakeVEthernetDevice(1400, fake_mac_4_0, True, 1)
        fake_v_eth_dev_4_1 = FakeVEthernetDevice(1401, fake_mac_4_1, False, 0)
        fake_v_disk_4_0 = FakeVDiskDevice(2400, True, 2,
                                          fake_backing_file_4_0, 1073741824)
        fake_floppy_back_file_4_0 = "[datastore01] test00/floppy_04.img"
        fake_v_floppy_4_0 = FakeVFloppyDevice(3400, False, 0,
                                              fake_floppy_back_file_4_0)

        fake_devs_vm_4 = [fake_v_eth_dev_4_0, fake_v_eth_dev_4_1,
                          fake_v_disk_4_0, fake_v_floppy_4_0]

        # VM5
        fake_back_file_5_0 = "[datastore01] test00/test_vm_050.vmdk"
        fake_v_disk_5_0 = FakeVDiskDevice(2500, True, 1,
                                          fake_back_file_5_0, 1073741824)
        fake_back_file_5_1 = "[datastore01] test00/test_vm_051.vmdk"
        fake_v_disk_5_1 = FakeVDiskDevice(2501, False, 0,
                                          fake_back_file_5_1, 2147483648)
        fake_back_file_5_2 = "[datastore01] test00/test_vm_052.vmdk"
        fake_v_disk_5_2 = FakeVDiskDevice(2502, False, 0,
                                          fake_back_file_5_2, 3221225472)
        fake_back_file_5_3 = "[datastore01] test00/test_vm_053.vmdk"
        fake_v_disk_5_3 = FakeVDiskDevice(2503, False, 0,
                                          fake_back_file_5_3, 4294967296)

        fake_v_sata_cntl_5_1 = FakeVSATAControllerDevice(4000,
                                                         "SATA Controller 1",
                                                         [2500, 2501])
        fake_v_sata_cntl_5_2 = FakeVSATAControllerDevice(4001,
                                                         "SATA Controller 2",
                                                         [2502])
        fake_v_scsi_cntl_5_1 = FakeVSCSIControllerDevice(4002,
                                                         "SCSI Controller 1",
                                                         [2503])

        fake_devs_vm_5 = [fake_v_disk_5_0, fake_v_disk_5_1,
                          fake_v_disk_5_2, fake_v_disk_5_3,
                          fake_v_sata_cntl_5_1, fake_v_sata_cntl_5_2,
                          fake_v_scsi_cntl_5_1]

        # VM6
        fake_v_cdrom_6_0 = FakeVCdromDevice(True, 1, None)
        fake_v_sata_cntl_6_0 = FakeVSATAControllerDevice(4000,
                                                         "SATA Controller 1",
                                                         [])
        fake_v_scsi_cntl_6_0 = FakeVSCSIControllerDevice(4002,
                                                         "SCSI Controller 1",
                                                         [])

        fake_devs_vm_6 = [fake_v_cdrom_6_0, fake_v_sata_cntl_6_0,
                          fake_v_scsi_cntl_6_0]

        # Initialize Fake VMware VMs
        fake_vm_0 = FakeVMWareVM(moid="vm-41", name="test_vm_00",
                                 uuid="06b95542-d018-11eb-b8bc-0242ac130003",
                                 is_powered=True, firmware=FakeFirmware.BIOS,
                                 memory_mb=2048, num_cpu=1,
                                 devices=fake_devs_vm_0)

        fake_vm_1 = FakeVMWareVM(moid="vm-42", name="test_vm_01",
                                 uuid="16b95542-d018-11eb-b8bc-0242ac130003",
                                 is_powered=False, firmware=FakeFirmware.UEFI,
                                 memory_mb=1024, num_cpu=6,
                                 devices=fake_devs_vm_1)

        fake_vm_2 = FakeVMWareVM(moid="vm-43", name="test_vm_02",
                                 uuid="26b95542-d018-11eb-b8bc-0242ac130003",
                                 is_powered=False, firmware=FakeFirmware.NONE,
                                 memory_mb=4096, num_cpu=4,
                                 devices=fake_devs_vm_2)

        fake_vm_3 = FakeVMWareVM(moid="vm-44", name="test_vm_03",
                                 uuid="36b95542-d018-11eb-b8bc-0242ac130003",
                                 is_powered=True, firmware=FakeFirmware.BIOS,
                                 memory_mb=8192, num_cpu=12,
                                 devices=fake_devs_vm_3)

        fake_vm_4 = FakeVMWareVM(moid="vm-45", name="test_vm_05",
                                 uuid="46b95542-d018-11eb-b8bc-0242ac130003",
                                 is_powered=True, firmware=FakeFirmware.BIOS,
                                 memory_mb=1024, num_cpu=2,
                                 devices=fake_devs_vm_4)

        fake_vm_5 = FakeVMWareVM(moid="vm-46", name="test_vm_06",
                                 uuid="56b95542-d018-11eb-b8bc-0242ac130003",
                                 is_powered=True, firmware=FakeFirmware.BIOS,
                                 memory_mb=1024, num_cpu=2,
                                 devices=fake_devs_vm_5)

        fake_vm_6 = FakeVMWareVM(moid="vm-47", name="test_vm_07",
                                 uuid="66b95542-d018-11eb-b8bc-0242ac130003",
                                 is_powered=True, firmware=FakeFirmware.BIOS,
                                 memory_mb=1024, num_cpu=2,
                                 devices=fake_devs_vm_6)

        # Setup Variables to use inside the unit tests
        self.unknown_vm_uuid = '00000000-0000-0000-0000-000000000000'
        self.unknown_vm_name = 'unknown_vm_name'
        self.test_vm_uuid = fake_vm_0.uuid
        self.test_vm_name = fake_vm_0.name
        self.powered_on_vm_name = fake_vm_0.name
        self.powered_off_vm_name = fake_vm_2.name
        self.pxe_boot_vm_name = fake_vm_0.name
        self.hdd_boot_vm_name = fake_vm_1.name
        self.cd_boot_vm_name = fake_vm_2.name
        self.no_boot_source_vm_name = fake_vm_3.name
        self.bios_vm_name = fake_vm_0.name
        self.uefi_vm_name = fake_vm_1.name
        self.firmware_none_vm_name = fake_vm_2.name
        self.two_gb_mem_vm_name = fake_vm_0.name
        self.eight_gb_mem_vm_name = fake_vm_3.name
        self.one_cpu_vm_name = fake_vm_0.name
        self.four_cpu_vm_name = fake_vm_2.name
        self.dual_nic_vm_name = fake_vm_4.name
        self.single_nic_vm_name = fake_vm_0.name
        self.no_nic_vm_name = fake_vm_5.name
        self.virtual_media_cd_vm_empty_name = fake_vm_1.name
        self.virtual_media_cd_vm_full_name = fake_vm_2.name
        self.virtual_media_cd_non_existent_name = fake_vm_5.name
        self.virtual_media_floppy_vm_full_name = fake_vm_4.name
        self.virtual_media_floppy_vm_empty_name = fake_vm_5.name
        self.disk_controller_vm_name = fake_vm_5.name
        self.disk_controller_empty_vm_name = fake_vm_6.name

        # Create an array of the fake vms
        self.fake_vms = [fake_vm_0, fake_vm_1, fake_vm_2, fake_vm_3,
                         fake_vm_4, fake_vm_5, fake_vm_6]

        # Find the index of fake vms inside the fake_vms array.
        self.powered_on_vm_index = self.fake_vms.index(fake_vm_0)
        self.powered_off_vm_index = self.fake_vms.index(fake_vm_2)
        self.pxe_boot_vm_index = self.fake_vms.index(fake_vm_0)
        self.hdd_boot_vm_index = self.fake_vms.index(fake_vm_1)
        self.cd_boot_vm_index = self.fake_vms.index(fake_vm_2)
        self.no_boot_source_vm_index = self.fake_vms.index(fake_vm_3)
        self.bios_vm_index = self.fake_vms.index(fake_vm_0)
        self.uefi_vm_index = self.fake_vms.index(fake_vm_1)
        self.firmware_none_vm_index = self.fake_vms.index(fake_vm_2)
        self.vmedia_cd_vm_empty_index = self.fake_vms.index(fake_vm_1)
        self.virtual_media_cd_vm_full_index = self.fake_vms.index(fake_vm_2)
        self.vmedia_cd_vm_non_existent_index = self.fake_vms.index(
            fake_vm_5)
        self.virtual_media_floppy_vm_full_index = self.fake_vms.index(
            fake_vm_4)
        self.virtual_media_floppy_vm_empty_index = self.fake_vms.index(
            fake_vm_5)

        # Number of boot devices per vm
        self.num_of_boot_devices_pxe_boot_vm = \
            sum(dev.bootable is True for dev in self.fake_vms[
                self.pxe_boot_vm_index].devices)

        self.num_of_boot_devices_hdd_boot_vm = \
            sum(dev.bootable is True for dev in self.fake_vms[
                self.hdd_boot_vm_index].devices)

        self.num_of_boot_devices_cd_boot_vm = \
            sum(dev.bootable is True for dev in self.fake_vms[
                self.cd_boot_vm_index].devices)

        # MAC Addresses
        self.dual_nic_vm_mac_1 = fake_mac_4_0
        self.dual_nic_vm_mac_2 = fake_mac_4_1
        self.single_nic_vm_mac = fake_mac_0_0

        # Backing ISO
        self.cd_boot_vm_backing_iso = fake_back_iso_2_0
        self.hdd_boot_vm_backing_file = fake_back_file_1_0

        # VirtualMedia Paths
        self.cd_virtual_media_iso_path = \
            "/tmp/virtual_media.iso"
        self.cd_virtual_media_hypervisor_path = \
            "[datastore01] vmedia/virtual_media.iso"
        self.floppy_virtual_media_image_path = \
            "/tmp/virtual_media.img"
        self.floppy_virtual_media_hypervisor_path = \
            "[datastore01] vmedia/virtual_media.img"

        # Simple Storage VM Expected Values
        self.simple_storage_vm_sata_cntl_one_label = \
            fake_v_sata_cntl_5_1.label.replace(" ", "-")
        self.vm_sata_cntl_one_id = \
            self.simple_storage_vm_sata_cntl_one_label
        self.vm_sata_cntl_one_name = \
            self.simple_storage_vm_sata_cntl_one_label
        self.vm_sata_cntl_one_dev_one_name = \
            fake_v_disk_5_0.backing_file
        self.vm_sata_cntl_one_dev_one_cap_in_b = \
            fake_v_disk_5_0.capacity_in_bytes
        self.vm_sata_cntl_one_dev_two_name = \
            fake_v_disk_5_1.backing_file
        self.vm_sata_cntl_one_dev_two_cap_in_b = \
            fake_v_disk_5_1.capacity_in_bytes

        self.simple_storage_vm_sata_cntl_two_label = \
            fake_v_sata_cntl_5_2.label.replace(" ", "-")
        self.vm_sata_cntl_two_id = \
            self.simple_storage_vm_sata_cntl_two_label
        self.vm_sata_cntl_two_name = \
            self.simple_storage_vm_sata_cntl_two_label
        self.vm_sata_cntl_two_dev_one_name = \
            fake_v_disk_5_2.backing_file
        self.vm_sata_cntl_two_dev_one_cap_in_b = \
            fake_v_disk_5_2.capacity_in_bytes

        self.simple_storage_vm_scsi_cntl_one_label = \
            fake_v_scsi_cntl_5_1.label.replace(" ", "-")
        self.vm_scsi_cntl_one_id = \
            self.simple_storage_vm_scsi_cntl_one_label
        self.vm_scsi_cntl_one_name = \
            self.simple_storage_vm_scsi_cntl_one_label
        self.vm_scsi_cntl_one_dev_one_name = \
            fake_v_disk_5_3.backing_file
        self.vm_scsi_cntl_one_dev_one_cap_in_b = \
            fake_v_disk_5_3.capacity_in_bytes

    def setUp(self):

        mock_host = None
        mock_port = None
        mock_username = None
        mock_password = None
        mock_vmedia_datastore = None

        test_driver_class = VmwareDriver.initialize({},
                                                    mock.MagicMock(),
                                                    mock_host, mock_port,
                                                    mock_username,
                                                    mock_password,
                                                    mock_vmedia_datastore)
        self.test_driver = test_driver_class()
        # Initialize test data. They are common across all test cases.
        # If specific test data are needed then the variable self.fake_vms
        # should NOT be used with the setup_service_instance method but it
        # should be defined within the test case explicitly.
        self.init_data()

        super(VMWareDriverTestCase, self).setUp()

    def tearDown(self):
        del self.test_driver
        del self.fake_vms
        del self.unknown_vm_uuid
        del self.unknown_vm_name
        del self.test_vm_uuid
        del self.test_vm_name
        del self.powered_on_vm_name
        del self.powered_off_vm_name
        del self.no_boot_source_vm_name
        del self.bios_vm_name
        del self.uefi_vm_name
        del self.firmware_none_vm_name
        del self.two_gb_mem_vm_name
        del self.eight_gb_mem_vm_name
        del self.one_cpu_vm_name
        del self.four_cpu_vm_name
        del self.dual_nic_vm_name
        del self.single_nic_vm_name
        del self.virtual_media_floppy_vm_full_name
        del self.virtual_media_floppy_vm_empty_name
        del self.virtual_media_cd_non_existent_name
        del self.disk_controller_vm_name
        del self.disk_controller_empty_vm_name

        del self.powered_on_vm_index
        del self.powered_off_vm_index
        del self.pxe_boot_vm_index
        del self.hdd_boot_vm_index
        del self.cd_boot_vm_index
        del self.no_boot_source_vm_index
        del self.firmware_none_vm_index
        del self.virtual_media_floppy_vm_full_index
        del self.virtual_media_floppy_vm_empty_index
        del self.vmedia_cd_vm_non_existent_index

        del self.num_of_boot_devices_pxe_boot_vm
        del self.num_of_boot_devices_hdd_boot_vm
        del self.num_of_boot_devices_cd_boot_vm

        del self.dual_nic_vm_mac_1
        del self.dual_nic_vm_mac_2
        del self.single_nic_vm_mac

        del self.cd_boot_vm_backing_iso
        del self.hdd_boot_vm_backing_file

        del self.cd_virtual_media_iso_path
        del self.cd_virtual_media_hypervisor_path
        del self.floppy_virtual_media_image_path
        del self.floppy_virtual_media_hypervisor_path

        del self.simple_storage_vm_sata_cntl_one_label
        del self.vm_sata_cntl_one_id
        del self.vm_sata_cntl_one_name
        del self.vm_sata_cntl_one_dev_one_name
        del self.vm_sata_cntl_one_dev_one_cap_in_b
        del self.vm_sata_cntl_one_dev_two_name
        del self.vm_sata_cntl_one_dev_two_cap_in_b

        del self.simple_storage_vm_sata_cntl_two_label
        del self.vm_sata_cntl_two_id
        del self.vm_sata_cntl_two_name
        del self.vm_sata_cntl_two_dev_one_name
        del self.vm_sata_cntl_two_dev_one_cap_in_b

        del self.simple_storage_vm_scsi_cntl_one_label
        del self.vm_scsi_cntl_one_id
        del self.vm_scsi_cntl_one_name
        del self.vm_scsi_cntl_one_dev_one_name
        del self.vm_scsi_cntl_one_dev_one_cap_in_b

        super(VMWareDriverTestCase, self).tearDown()

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_connection_error(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        # Mock enter method with VMWAREDRV_ERR_040 Connection error
        mock_error_msg = FakeException.VMWARE_CONN_MOCK_EXCEPTION.value
        error_msg = ErrMsg.ERR_VMWARE_OPEN.format(mock_error_msg)

        vm_mock.__enter__.side_effect = Exception(error_msg)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        self.assertRaisesRegex(Exception, error_msg,
                               self.test_driver.set_boot_mode,
                               self.bios_vm_name, "Unknown")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_uuid_found(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        returned_uuid_0 = self.test_driver.uuid(self.test_vm_uuid)

        # Assert UUID
        self.assertEqual(self.test_vm_uuid, returned_uuid_0)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_uuid_notfound(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_000
        # by calling the Mocked Driver
        msg = ErrMsg.IDENTITY_NOT_FOUND.format(self.unknown_vm_name)
        self.assertRaisesRegex(error.FishyError, msg, self.test_driver.uuid,
                               self.unknown_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_uuid_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.uuid, self.test_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_name_found(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        returned_name = self.test_driver.name(self.test_vm_uuid)

        # Assert
        self.assertEqual(self.test_vm_name, returned_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_name_notfound(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_000
        # by calling the Mocked Driver
        msg = ErrMsg.IDENTITY_NOT_FOUND.format(
            self.unknown_vm_uuid)
        self.assertRaisesRegex(error.FishyError, msg, self.test_driver.name,
                               self.unknown_vm_uuid)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_name_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.name, self.test_vm_uuid)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_power_state_on(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        returned_power_state = self.test_driver.get_power_state(
            self.powered_on_vm_name)

        # Assert
        self.assertEqual('On', returned_power_state)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_power_state_off(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        returned_power_state = self.test_driver.get_power_state(
            self.powered_off_vm_name)

        # Assert
        self.assertEqual('Off', returned_power_state)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_power_state_not_found(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_000
        # by calling the Mocked Driver
        msg = ErrMsg.IDENTITY_NOT_FOUND.format(
            self.unknown_vm_name)
        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.get_power_state,
                               self.unknown_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_power_state_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.get_power_state,
                               self.test_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_on(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        self.test_driver.set_power_state(self.powered_off_vm_name,
                                         PowerStates.ON.value)

        # Assert
        vms[self.powered_off_vm_index].PowerOn.assert_called_once()

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_forceon(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        self.test_driver.set_power_state(self.powered_off_vm_name,
                                         PowerStates.FORCE_ON.value)

        # Assert
        vms[self.powered_off_vm_index].PowerOn.assert_called_once()

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_forceoff(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        self.test_driver.set_power_state(self.powered_on_vm_name,
                                         PowerStates.FORCE_OFF.value)

        # Assert
        vms[self.powered_on_vm_index].PowerOff.assert_called_once()

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_graceful_shutdown(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        self.test_driver.set_power_state(self.powered_on_vm_name,
                                         PowerStates.
                                         GRACEFUL_SHUTDOWN.value)

        # Assert
        vms[self.powered_on_vm_index].ShutdownGuest.assert_called_once()

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_graceful_restart(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        self.test_driver.set_power_state(self.powered_on_vm_name,
                                         PowerStates.
                                         GRACEFUL_RESTART.value)

        # Assert
        vms[self.powered_on_vm_index].RebootGuest.assert_called_once()

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_force_restart(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        self.test_driver.set_power_state(self.powered_on_vm_name,
                                         PowerStates.FORCE_RESTART.value)

        # Assert
        vms[self.powered_on_vm_index].ResetVM_Task.assert_called_once()

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_nmi(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Call the Mocked Driver
        self.test_driver.set_power_state(self.powered_on_vm_name,
                                         PowerStates.NMI.value)

        # Assert
        vms[self.powered_on_vm_index].SendNMI.assert_called_once()

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.set_power_state,
                               self.powered_on_vm_name,
                               PowerStates.ON.value)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_vm_power_on_method_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Mock vm power method to raise an exception
        vms[self.powered_off_vm_index].PowerOn.side_effect = \
            Exception('Mocked Error!')

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_010
        # by calling the Mocked Driver
        msg = ErrMsg.POWER_STATE_CANNOT_SET.format(
            PowerStates.ON.value,
            self.powered_off_vm_name)

        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.set_power_state,
                               self.powered_off_vm_name,
                               PowerStates.ON.value)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_vm_power_forceon_method_ex(self,
                                                        vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Mock vm power method to raise an exception
        vms[self.powered_off_vm_index].PowerOn.side_effect = \
            Exception('Mocked Error!')

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_010
        # by calling the Mocked Driver
        msg = ErrMsg.POWER_STATE_CANNOT_SET.format(
            PowerStates.FORCE_ON.value, self.powered_off_vm_name)
        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.set_power_state,
                               self.powered_off_vm_name,
                               PowerStates.FORCE_ON.value)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_vm_power_forceoff_method_ex(self,
                                                         vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Mock vm power method to raise an exception
        vms[self.powered_on_vm_index].PowerOff.side_effect = \
            Exception('Mocked Error!')

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_010
        # by calling the Mocked Driver
        msg = ErrMsg.POWER_STATE_CANNOT_SET.format(
            PowerStates.FORCE_OFF.value,
            self.powered_on_vm_name)
        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.set_power_state,
                               self.powered_on_vm_name,
                               PowerStates.FORCE_OFF.value)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_vm_power_grace_shut_method_ex(self,
                                                           vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Mock vm power method to raise an exception
        vms[self.powered_on_vm_index].ShutdownGuest.side_effect = \
            Exception('Mocked Error!')

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_010
        # by calling the Mocked Driver
        msg = ErrMsg.POWER_STATE_CANNOT_SET.format(
            PowerStates.GRACEFUL_SHUTDOWN.value,
            self.powered_on_vm_name)
        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.set_power_state,
                               self.powered_on_vm_name,
                               PowerStates.GRACEFUL_SHUTDOWN.value)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_vm_power_grace_restart_method_ex(self,
                                                              vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Mock vm power method to raise an exception
        vms[self.powered_on_vm_index].RebootGuest.side_effect = \
            Exception('Mocked Error!')

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_010
        # by calling the Mocked Driver
        msg = ErrMsg.POWER_STATE_CANNOT_SET.format(
            PowerStates.GRACEFUL_RESTART.value, self.powered_on_vm_name)
        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.set_power_state,
                               self.powered_on_vm_name,
                               PowerStates.GRACEFUL_RESTART.value)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_vm_power_force_restart_method_ex(self,
                                                              vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Mock vm power method to raise an exception
        vms[self.powered_on_vm_index].ResetVM_Task.side_effect = \
            Exception('Mocked Error!')

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_010
        # by calling the Mocked Driver
        msg = ErrMsg.POWER_STATE_CANNOT_SET.format(
            PowerStates.FORCE_RESTART.value, self.powered_on_vm_name)
        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.set_power_state,
                               self.powered_on_vm_name,
                               PowerStates.FORCE_RESTART.value)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_power_state_vm_power_nmi_method_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Mock vm power method to raise an exception
        vms[self.powered_on_vm_index].SendNMI.side_effect = \
            Exception('Mocked Error!')

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_010
        # by calling the Mocked Driver
        msg = ErrMsg.POWER_STATE_CANNOT_SET.format(
            PowerStates.NMI.value, self.powered_on_vm_name)
        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.set_power_state,
                               self.test_vm_name, PowerStates.NMI.value)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_device_pxe(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        returned_boot_device = self.test_driver.get_boot_device(
            self.pxe_boot_vm_name)

        # Assert UUIDs of 2 out of 3 vms.
        self.assertEqual("Pxe", returned_boot_device)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_device_hdd(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        returned_boot_device = self.test_driver.get_boot_device(
            self.hdd_boot_vm_name)

        # Assert UUIDs of 2 out of 3 vms.
        self.assertEqual("Hdd", returned_boot_device)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_device_cd(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        returned_boot_device = self.test_driver.get_boot_device(
            self.cd_boot_vm_name)

        # Assert UUIDs of 2 out of 3 vms.
        self.assertEqual("Cd", returned_boot_device)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_device_pxe(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        self.test_driver.set_boot_device(self.cd_boot_vm_name, "Pxe")

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.cd_boot_vm_index].ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the bootOrder from the calling arguments of
        # the mocked vms ReconfigVM_Task method and then check if it is in the
        # expected order.
        # assert_called_with does not work. expected and actual objects are
        # complex and do not equal. Not easy to re-create the calling arguments
        # of the vim.VirtualMachineConfigSpec
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[
            self.cd_boot_vm_index].ReconfigVM_Task.call_args[0]
        # reconfig_vm_args is a tuple so we need to get the first item which
        # is the vim.vm.ConfigSpec and then grab the bootOptions.bootOrder
        actual_boot_order = reconfig_vm_args[0].bootOptions.bootOrder

        # 2. Check that the bootOrder is of length 2. The cd boot vm has
        # one boot device of Cd by default. We set the boot device to pxe so
        # we are adding a new boot device in the boot order.
        num_of_expected_boot_devices = self.num_of_boot_devices_cd_boot_vm + 1
        self.assertEqual(num_of_expected_boot_devices,
                         len(actual_boot_order),
                         "Number of devices in the Boot order is not correct.")

        # 3. Check that the boot order is correct
        # First device should be Cd
        self.assertTrue(isinstance(actual_boot_order[0],
                                   vim.vm.BootOptions.BootableEthernetDevice),
                        "First Device is not BootableEtherDevice")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_device_hdd(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        self.test_driver.set_boot_device(self.pxe_boot_vm_name, "Hdd")

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.pxe_boot_vm_index].ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the bootOrder from the calling arguments
        # of the mocked vms ReconfigVM_Task method and then check if it is in
        # the expected order.
        # assert_called_with does not work. expected and actual objects are
        # complex and do not equal. Not easy to re-create the calling arguments
        # of the vim.VirtualMachineConfigSpec
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[
            self.pxe_boot_vm_index].ReconfigVM_Task.call_args[0]
        # reconfig_vm_args is a tuple so we need to get the first item which is
        #  the vim.vm.ConfigSpec and then grab the bootOptions.bootOrder
        actual_boot_order = reconfig_vm_args[0].bootOptions.bootOrder

        # 2. Check that the bootOrder is of length 3.
        self.assertEqual(self.num_of_boot_devices_pxe_boot_vm,
                         len(actual_boot_order),
                         "Number of devices in the Boot order is not correct.")

        # 3. Check that the boot order is correct
        # First device should be Cd
        self.assertTrue(isinstance(actual_boot_order[0],
                                   vim.vm.BootOptions.BootableDiskDevice),
                        "First Device is not Disk")
        # Second device should be Pxe
        self.assertTrue(isinstance(actual_boot_order[1],
                                   vim.vm.BootOptions.BootableEthernetDevice),
                        "Second Device is not Pxe")
        # Third device should be Disk
        self.assertTrue(isinstance(actual_boot_order[2],
                                   vim.vm.BootOptions.BootableCdromDevice),
                        "First Device is not Cd")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_device_cd(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Set the boot device Cd to a vm which boots with another device.
        # Pxe Boot in this instance.
        self.test_driver.set_boot_device(self.pxe_boot_vm_name, "Cd")

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.pxe_boot_vm_index].ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the bootOrder from the calling arguments of
        # the mocked vms ReconfigVM_Task method and then check if it is in the
        # expected order.
        # assert_called_with does not work. expected and actual objects are
        # complex and do not equal. Not easy to re-create the calling arguments
        # of the vim.VirtualMachineConfigSpec
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[
            self.pxe_boot_vm_index].ReconfigVM_Task.call_args[0]

        # reconfig_vm_args is a tuple so we need to get the first item which is
        # the vim.vm.ConfigSpec and then grab the bootOptions.bootOrder
        actual_boot_order = reconfig_vm_args[0].bootOptions.bootOrder

        # 2. Check that the bootOrder is of length 3.
        self.assertEqual(self.num_of_boot_devices_pxe_boot_vm,
                         len(actual_boot_order),
                         "Number of devices in the Boot order is not correct.")

        # 3. Check that the boot order is correct
        # First device should be Cd
        self.assertTrue(isinstance(actual_boot_order[0],
                                   vim.vm.BootOptions.BootableCdromDevice),
                        "First Device is not Cd")
        # Second device should be Pxe
        self.assertTrue(isinstance(actual_boot_order[1],
                                   vim.vm.BootOptions.BootableEthernetDevice),
                        "Second Device is not Pxe")
        # Third device should be Disk
        self.assertTrue(isinstance(actual_boot_order[2],
                                   vim.vm.BootOptions.BootableDiskDevice),
                        "First Device is not Disk")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_device_raises_invalid_boot_source(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_010
        # by calling the Mocked Driver
        msg = ErrMsg.INVALID_BOOT_SOURCE.format(
            "Cdd")
        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.set_boot_device,
                               self.pxe_boot_vm_name, "Cdd")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_device_raises_no_v_dev_to_support_boot_src(self,
                                                                 vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for error.FishyError VMWAREDRV_ERR_010
        # by calling the Mocked Driver
        msg = ErrMsg.\
            NO_VIRT_DEV_TO_SUPPORT_BOOT_SRC.format(
                self.no_boot_source_vm_name, "Cd")
        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.set_boot_device,
                               self.no_boot_source_vm_name, "Cd")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_mode_legacy(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        returned_boot_mode = self.test_driver.get_boot_mode(self.bios_vm_name)

        self.assertEqual("Legacy", returned_boot_mode)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_mode_uefi(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        returned_boot_mode = self.test_driver.get_boot_mode(self.uefi_vm_name)

        self.assertEqual("UEFI", returned_boot_mode)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_mode_none(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        returned_boot_mode = self.test_driver.get_boot_mode(
            self.firmware_none_vm_name)

        self.assertEqual("None", returned_boot_mode)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_mode_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.get_boot_mode,
                               self.uefi_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_mode_legacy(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Set the boot mode to Legacy
        self.test_driver.set_boot_mode(self.uefi_vm_name, "Legacy")

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.uefi_vm_index].ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the firmware from the calling arguments
        # of the mocked vms ReconfigVM_Task method and then
        # check if it is the expected one.
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[
            self.uefi_vm_index].ReconfigVM_Task.call_args[0]
        # reconfig_vm_args is a tuple so we need to get the first item which is
        #  the vim.vm.ConfigSpec and then grab the firmware
        actual_firmware = reconfig_vm_args[0].firmware

        # 2. Check that the correct firmware was passed as a parameter.
        self.assertEqual(vim.GuestOsDescriptorFirmwareType.bios,
                         actual_firmware, "Firmware is not correct.")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_mode_uefi(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Set the boot mode to Legacy
        self.test_driver.set_boot_mode(self.bios_vm_name, "UEFI")

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.bios_vm_index].ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the firmware from the calling arguments of
        # the mocked vms ReconfigVM_Task method and then
        # check if it is the expected one.
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[
            self.bios_vm_index].ReconfigVM_Task.call_args[0]
        # reconfig_vm_args is a tuple so we need to get the first item which
        # is the vim.vm.ConfigSpec and then grab the firmware
        actual_firmware = reconfig_vm_args[0].firmware

        # 2. Check that the correct firmware was passed as a parameter.
        self.assertEqual(vim.GuestOsDescriptorFirmwareType.efi,
                         actual_firmware, "Firmware is not correct.")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_mode_invalid_boot_mode_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Set the boot mode to Unknown
        msg = ErrMsg.INVALID_BOOT_MODE
        self.assertRaisesRegex(error.FishyError, msg,
                               self.test_driver.set_boot_mode,
                               self.bios_vm_name, "Unknown")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_mode_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.set_boot_mode,
                               self.bios_vm_name, "Unknown")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_total_memory(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        returned_mem_in_gib_2 = self.test_driver.get_total_memory(
            self.two_gb_mem_vm_name)
        returned_mem_in_gib_8 = self.test_driver.get_total_memory(
            self.eight_gb_mem_vm_name)

        self.assertEqual(2, returned_mem_in_gib_2)
        self.assertEqual(8, returned_mem_in_gib_8)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_total_memory_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.get_total_memory,
                               self.two_gb_mem_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_total_cpus(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        returned_num_cpu_1 = self.test_driver.get_total_cpus(
            self.one_cpu_vm_name)
        returned_num_cpu_4 = self.test_driver.get_total_cpus(
            self.four_cpu_vm_name)

        self.assertEqual(1, returned_num_cpu_1)
        self.assertEqual(4, returned_num_cpu_4)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_total_cpus_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.get_total_cpus,
                               self.one_cpu_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_bios(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = ErrMsg.\
            DRV_OP_NOT_SUPPORTED.format("BIOS Settings")
        self.assertRaisesRegex(error.NotSupportedError,
                               mock_error_message,
                               self.test_driver.get_bios, self.test_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_bios(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = \
            ErrMsg.DRV_OP_NOT_SUPPORTED.format(
                "BIOS Settings")
        self.assertRaisesRegex(error.NotSupportedError,
                               mock_error_message,
                               self.test_driver.set_bios,
                               self.test_vm_name, {})

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_reset_bios(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = ErrMsg.\
            DRV_OP_NOT_SUPPORTED.format("BIOS Settings")
        self.assertRaisesRegex(error.NotSupportedError, mock_error_message,
                               self.test_driver.reset_bios, self.test_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_nics(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Get the vm nics
        observed_nics_dual_nic = self.test_driver.get_nics(
            self.dual_nic_vm_name)
        observed_nics_single_nic = self.test_driver.get_nics(
            self.single_nic_vm_name)

        # Assert
        dual_nic_vm_nic_one_mac = self.dual_nic_vm_mac_1
        dual_nic_vm_nic_two_mac = self.dual_nic_vm_mac_2
        dual_nic_vm_expected_nics = [{'id': dual_nic_vm_nic_one_mac,
                                      'mac': dual_nic_vm_nic_one_mac}, {
            'id': dual_nic_vm_nic_two_mac, 'mac': dual_nic_vm_nic_two_mac}]

        single_vm_nic_mac = self.single_nic_vm_mac
        single_nic_vm_expected_nic = [
            {'id': single_vm_nic_mac, 'mac': single_vm_nic_mac}, ]

        self.assertEqual(dual_nic_vm_expected_nics,
                         observed_nics_dual_nic, "NIC list is not the same.")
        self.assertEqual(single_nic_vm_expected_nic,
                         observed_nics_single_nic,
                         "NIC list is not the same.")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_nics_no_nics(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Get the vm nics
        no_nic_vm_observed_nics = self.test_driver.get_nics(
            self.no_nic_vm_name)

        # Assert
        no_nic_vm_expected_nics = []
        self.assertEqual(no_nic_vm_expected_nics,
                         no_nic_vm_observed_nics, "NIC list is not the same.")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_nics_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.get_nics,
                               self.single_nic_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_image_cd(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        observed_boot_image = self.test_driver.get_boot_image(
            self.cd_boot_vm_name, "Cd")

        observed_image = observed_boot_image[0]
        observed_writed_protected = observed_boot_image[1]
        observed_inserted = observed_boot_image[2]

        # Assert
        self.assertEqual(self.cd_boot_vm_backing_iso,
                         observed_image, "Image is not correct.")
        self.assertEqual(True, observed_writed_protected,
                         "Write-protected value is not correct.")
        self.assertEqual(True, observed_inserted,
                         "Inserted value is not correct.")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_image_hdd(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        observed_boot_image = self.test_driver.get_boot_image(
            self.hdd_boot_vm_name, "Hdd")

        observed_image = observed_boot_image[0]
        observed_writed_protected = observed_boot_image[1]
        observed_inserted = observed_boot_image[2]

        # Assert
        self.assertEqual(self.hdd_boot_vm_backing_file,
                         observed_image, "Image is not correct.")
        self.assertEqual(False, observed_writed_protected,
                         "Write-protected value is not correct.")
        self.assertEqual(False, observed_inserted,
                         "Inserted value is not correct.")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_image_pxe(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        observed_boot_image = self.test_driver.get_boot_image(
            self.hdd_boot_vm_name, "Pxe")

        observed_image = observed_boot_image[0]
        observed_writed_protected = observed_boot_image[1]
        observed_inserted = observed_boot_image[2]

        # Assert
        self.assertEqual("", observed_image, "Image is not correct.")
        self.assertEqual(False, observed_writed_protected,
                         "Write-protected value is not correct.")
        self.assertEqual(False, observed_inserted,
                         "Inserted value is not correct.")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_boot_image_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.get_boot_image,
                               self.hdd_boot_vm_name, "Pxe")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_cd_insert_media_empty(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Mock the _upload_image helper method. There is no real esxi so we
        # cannot upload the iso to a data store.
        self.test_driver._upload_image = mock.MagicMock(
            return_value=self.cd_virtual_media_hypervisor_path)

        self.test_driver.set_boot_image(
            self.virtual_media_cd_vm_empty_name, "Cd",
            self.cd_virtual_media_iso_path)

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.hdd_boot_vm_index].ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the bootOrder from the calling arguments of
        # the mocked vms ReconfigVM_Task method and then check if it contains
        # the expected values.
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[
            self.vmedia_cd_vm_empty_index].ReconfigVM_Task.call_args[0]

        # reconfig_vm_args is a tuple so we need to get the first item which is
        # the vim.VirtualDeviceConfigSpec() and then grab the deviceChange
        observed_device_change_list = reconfig_vm_args[0].deviceChange

        # 2. Check that the deviceChange list is of size 1. We have not updated
        # any more devices.
        self.assertEqual(1, len(observed_device_change_list),
                         "Device Change List must be 1")

        observed_dev_chg_op = observed_device_change_list[0].operation

        # 3. Check that the operation is set to edit
        self.assertEqual("edit", observed_dev_chg_op,
                         "Device Change operation must be edit")

        # Get Device from the change device list
        observed_device_change_device = observed_device_change_list[0].device

        # 4. Check that the device is connected
        observed_dev_connected = \
            observed_device_change_device.connectable.connected
        self.assertEqual(True, observed_dev_connected,
                         "Connected must be true")

        # 5. Check that the device is startConnected
        observed_dev_start_connected = \
            observed_device_change_device.connectable.startConnected
        self.assertEqual(True, observed_dev_start_connected,
                         "Start Connected must be true")

        # 6. Check that the backing is IsoBackingInfo.
        # Get the backing from the device
        observed_device_backing = observed_device_change_device.backing
        # The backing is a MagicMock so we need to use the __class__ attribute
        # of the mock to get the spec class. (The class we are mocking)
        # The spec class in this case comes back as
        # pyVmomi.VmomiSupport.LazyType and we cannot check type equality.
        # It is a special wrapper class of pyvimomi for lazy loading and we
        # need to load the real class to get its type.
        # We need to use VmomiSupport.GetVmodlType for this to work.
        # See more info at pyvimomi source code below.
        # https://github.com/vmware/pyvmomi/blob/
        # b9a96ca0c64c4833c23859e998c2e64e0952eac7/pyVmomi/VmomiSupport.py#L206
        observed_dev_back_nonlazy_load_type = VmomiSupport.GetVmodlType(
            observed_device_backing.__class__)

        self.assertEqual(type(vim.vm.device.VirtualCdrom.IsoBackingInfo(
        )), observed_dev_back_nonlazy_load_type,
            "Backing Type is not correct")

        # 7. Check that the backing file name is the same as in the hypervisor
        observed_dev_back_filename = \
            observed_device_change_device.backing.fileName
        self.assertEqual(self.cd_virtual_media_hypervisor_path,
                         observed_dev_back_filename,
                         "Backing filename is not correct")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_cd_insert_media_full(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Mock the _upload_image helper method. There is no real esxi so we
        # cannot upload the iso to a data store.
        self.test_driver._upload_image = mock.MagicMock(
            return_value=self.cd_virtual_media_hypervisor_path)

        self.test_driver.set_boot_image(
            self.virtual_media_cd_vm_full_name, "Cd",
            self.cd_virtual_media_iso_path)

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.virtual_media_cd_vm_full_index].\
            ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the bootOrder from the calling arguments of
        # the mocked vms ReconfigVM_Task method and then check if it contains
        # the expected values.
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[
            self.virtual_media_cd_vm_full_index].ReconfigVM_Task.call_args[0]

        # reconfig_vm_args is a tuple so we need to get the first item which is
        # the vim.VirtualDeviceConfigSpec() and then grab the deviceChange
        observed_dev_change_list = reconfig_vm_args[0].deviceChange

        # 2. Check that the deviceChange list is of size 1. We have not updated
        # any more devices.
        self.assertEqual(1, len(observed_dev_change_list),
                         "Device Change List must be 1")

        observed_device_change_operation = \
            observed_dev_change_list[0].operation

        # 3. Check that the operation is set to edit
        self.assertEqual("edit", observed_device_change_operation,
                         "Device Change operation must be edit")

        # Get Device from the change device list
        observed_dev_chg_dev = observed_dev_change_list[0].device

        # 4. Check that the device is connected
        observed_device_connected = observed_dev_chg_dev.connectable.connected
        self.assertEqual(True, observed_device_connected,
                         "Connected must be true")

        # 5. Check that the device is startConnected
        observed_dev_start_connected = \
            observed_dev_chg_dev.connectable.startConnected
        self.assertEqual(True, observed_dev_start_connected,
                         "Start Connected must be true")

        # 6. Check that the backing is IsoBackingInfo.
        # Get the backing from the device
        observed_device_backing = observed_dev_chg_dev.backing
        # The backing is a MagicMock so we need to use the __class__ attribute
        # of the mock to get the spec class. (The class we are mocking)
        # The spec class in this case comes back as
        # pyVmomi.VmomiSupport.LazyType and we cannot check type equality.
        # It is a special wrapper class of pyvimomi for lazy loading and we
        # need to load the real class to get its type.
        # We need to use VmomiSupport.GetVmodlType for this to work.
        # See more info at pyvimomi source code below.
        # https://github.com/vmware/pyvmomi/blob/
        # b9a96ca0c64c4833c23859e998c2e64e0952eac7/pyVmomi/VmomiSupport.py#L206
        observed_device_backing_nonlazy_load_type = VmomiSupport.GetVmodlType(
            observed_device_backing.__class__)
        self.assertEqual(type(vim.vm.device.VirtualCdrom.IsoBackingInfo(
        )), observed_device_backing_nonlazy_load_type,
            "Backing Type is not correct")

        # 7. Check that the backing file name is the same as in the hypervisor
        observed_device_backing_filename = \
            observed_dev_chg_dev.backing.fileName
        self.assertEqual(self.cd_virtual_media_hypervisor_path,
                         observed_device_backing_filename,
                         "Backing filename is not correct")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_cd_insert_media_non_existent(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Mock the _upload_image helper method. There is no real esxi so we
        # cannot upload the iso to a data store.
        self.test_driver._upload_image = mock.MagicMock(
            return_value=self.cd_virtual_media_hypervisor_path)

        self.test_driver.set_boot_image(
            self.virtual_media_cd_non_existent_name, "Cd",
            self.cd_virtual_media_iso_path)

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.vmedia_cd_vm_non_existent_index].\
            ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the bootOrder from the calling arguments of
        # the mocked vms ReconfigVM_Task method and then check if it contains
        # the expected values.
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[self.vmedia_cd_vm_non_existent_index].\
            ReconfigVM_Task.call_args[0]

        # reconfig_vm_args is a tuple so we need to get the first item which is
        # the vim.VirtualDeviceConfigSpec() and then grab the deviceChange
        observed_dev_change_list = reconfig_vm_args[0].deviceChange

        # 2. Check that the deviceChange list is of size 1. We have not updated
        # any more devices.
        self.assertEqual(1, len(observed_dev_change_list),
                         "Device Change List must be 1")

        observed_dev_chg_op = observed_dev_change_list[0].operation

        # 3. Check that the operation is set to edit
        self.assertEqual("add", observed_dev_chg_op,
                         "Device Change operation must be edit")

        # Get Device from the change device list
        observed_dev_change_device = observed_dev_change_list[0].device

        # 4. Check that the device is connected
        observed_dev_connected = \
            observed_dev_change_device.connectable.connected
        self.assertEqual(True, observed_dev_connected,
                         "Connected must be true")

        # 5. Check that the device is startConnected
        observed_dev_start_connected = \
            observed_dev_change_device.connectable.startConnected
        self.assertEqual(True, observed_dev_start_connected,
                         "Start Connected must be true")

        # 6. Check that the backing is IsoBackingInfo.
        # Get the backing from the device
        observed_device_backing = observed_dev_change_device.backing
        # The backing is a MagicMock so we need to use the __class__ attribute
        # of the mock to get the spec class. (The class we are mocking)
        # The spec class in this case comes back as
        # pyVmomi.VmomiSupport.LazyType and we cannot check type equality.
        # It is a special wrapper class of pyvimomi for lazy loading and we
        # need to load the real class to get its type.
        # We need to use VmomiSupport.GetVmodlType for this to work.
        # See more info at pyvimomi source code below.
        # https://github.com/vmware/pyvmomi/blob/
        # b9a96ca0c64c4833c23859e998c2e64e0952eac7/pyVmomi/VmomiSupport.py#L206
        observed_device_backing_nonlazy_load_type = VmomiSupport.GetVmodlType(
            observed_device_backing.__class__)
        self.assertEqual(type(vim.vm.device.VirtualCdrom.IsoBackingInfo(
        )), observed_device_backing_nonlazy_load_type,
            "Backing Type is not correct")

        # 7. Check that the backing file name is the same as in the hypervisor
        observed_device_backing_filename = \
            observed_dev_change_device.backing.fileName
        self.assertEqual(self.cd_virtual_media_hypervisor_path,
                         observed_device_backing_filename,
                         "Backing filename is not correct")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_cd_eject_media(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Mock the _upload_image helper method. There is no real esxi so we
        # cannot upload the iso to a data store.
        self.test_driver._upload_image = mock.MagicMock(
            return_value=self.cd_virtual_media_hypervisor_path)

        self.test_driver.set_boot_image(
            self.virtual_media_cd_vm_full_name, "Cd")

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.virtual_media_cd_vm_full_index].\
            ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the bootOrder from the calling arguments of
        # the mocked vms ReconfigVM_Task method and then check if it contains
        # the expected values.
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[self.virtual_media_cd_vm_full_index].\
            ReconfigVM_Task.call_args[0]

        # reconfig_vm_args is a tuple so we need to get the first item which is
        # the vim.VirtualDeviceConfigSpec() and then grab the deviceChange
        observed_device_change_list = reconfig_vm_args[0].deviceChange

        # 2. Check that the deviceChange list is of size 1. We have not updated
        # any more devices.
        self.assertEqual(1, len(observed_device_change_list),
                         "Device Change List must be 1")

        observed_dev_chg_op = observed_device_change_list[0].operation

        # 3. Check that the operation is set to edit
        self.assertEqual("edit", observed_dev_chg_op,
                         "Device Change operation must be edit")

        # Get Device from the change device list
        observed_dev_chg_dev = observed_device_change_list[0].device

        # 4. Check that the device is connected
        observed_dev_connected = observed_dev_chg_dev.connectable.connected
        self.assertEqual(True, observed_dev_connected,
                         "Connected must be true")

        # 5. Check that the device is startConnected
        observed_device_start_connected = \
            observed_dev_chg_dev.connectable.startConnected
        self.assertEqual(True, observed_device_start_connected,
                         "Start Connected must be true")

        # 6. Check that the device backing is useAutoDetect
        observed_device_use_auto_detected = \
            observed_dev_chg_dev.backing.useAutoDetect
        self.assertEqual(True, observed_device_use_auto_detected,
                         "Backing useAutoDetect must be true")

        # 7. Check that the backing is AtapiBackingInfo.
        # We are removing the iso
        # Get the backing from the device
        observed_device_backing = observed_dev_chg_dev.backing
        # The backing is a MagicMock so we need to use the __class__ attribute
        # of the mock to get the spec class. (The class we are mocking)
        # The spec class in this case comes back
        # as pyVmomi.VmomiSupport.LazyType and we cannot check type equality.
        # It is a special wrapper class of pyvimomi for lazy loading and we
        # need to load the real class to get its type.
        # We need to use VmomiSupport.GetVmodlType for this to work.
        # See more info at pyvimomi source code below.
        # https://github.com/vmware/pyvmomi/blob/
        # b9a96ca0c64c4833c23859e998c2e64e0952eac7/pyVmomi/VmomiSupport.py#L206
        observed_device_backing_nonlazy_load_type = VmomiSupport.GetVmodlType(
            observed_device_backing.__class__)

        self.assertEqual(type(vim.vm.device.VirtualCdrom.AtapiBackingInfo(
        )), observed_device_backing_nonlazy_load_type,
            "Backing Type is not correct")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_floppy_insert_media_full(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Mock the _upload_image helper method. There is no real esxi so we
        # cannot upload the iso to a data store.
        self.test_driver._upload_image = mock.MagicMock(
            return_value=self.floppy_virtual_media_hypervisor_path)

        self.test_driver.set_boot_image(
            self.virtual_media_floppy_vm_full_name, "Floppy",
            self.floppy_virtual_media_image_path)

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.virtual_media_floppy_vm_full_index].\
            ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the bootOrder from the calling arguments of
        # the mocked vms ReconfigVM_Task method and then check if it contains
        # the expected values.
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[self.virtual_media_floppy_vm_full_index].\
            ReconfigVM_Task.call_args[0]

        # reconfig_vm_args is a tuple so we need to get the first item which is
        # the vim.VirtualDeviceConfigSpec() and then grab the deviceChange
        observed_device_change_list = reconfig_vm_args[0].deviceChange

        # 2. Check that the deviceChange list is of size 1. We have not updated
        # any more devices.
        self.assertEqual(1, len(observed_device_change_list),
                         "Device Change List must be 1")

        observed_device_change_operation = \
            observed_device_change_list[0].operation

        # 3. Check that the operation is set to edit
        self.assertEqual("edit", observed_device_change_operation,
                         "Device Change operation must be edit")

        # Get Device from the change device list
        observed_dev_chg_dev = observed_device_change_list[0].device

        # 1. Check that the backing file name is the same as in the hypervisor
        observed_device_backing_filename = \
            observed_dev_chg_dev.backing.fileName
        self.assertEqual(self.floppy_virtual_media_hypervisor_path,
                         observed_device_backing_filename,
                         "Backing filename is not correct")

        # 2. Check that the backing is ImageBackingInfo.
        # Get the backing from the device
        observed_device_backing = observed_dev_chg_dev.backing
        # The backing is a MagicMock so we need to use the __class__ attribute
        # of the mock to get the spec class. (The class we are mocking)
        # The spec class in this case comes back as
        # pyVmomi.VmomiSupport.LazyType and we cannot check type equality.
        # It is a special wrapper class of pyvimomi for lazy loading and we
        # need to load the real class to get its type.
        # We need to use VmomiSupport.GetVmodlType for this to work.
        # See more info at pyvimomi source code below.
        # https://github.com/vmware/pyvmomi/blob/
        # b9a96ca0c64c4833c23859e998c2e64e0952eac7/pyVmomi/VmomiSupport.py#L206
        observed_device_backing_nonlazy_load_type = VmomiSupport.GetVmodlType(
            observed_device_backing.__class__)

        self.assertEqual(type(vim.vm.device.VirtualFloppy.ImageBackingInfo(
        )), observed_device_backing_nonlazy_load_type,
            "Backing Type is not correct")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_floppy_insert_media_empty(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Mock the _upload_image helper method. There is no real esxi so we
        # cannot upload the iso to a data store.
        self.test_driver._upload_image = mock.MagicMock(
            return_value=self.floppy_virtual_media_hypervisor_path)

        self.test_driver.set_boot_image(
            self.virtual_media_floppy_vm_empty_name, "Floppy",
            self.floppy_virtual_media_image_path)

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.virtual_media_floppy_vm_empty_index].\
            ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the bootOrder from the calling arguments of
        # the mocked vms ReconfigVM_Task method and then check if it contains
        # the expected values.
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[self.virtual_media_floppy_vm_empty_index].\
            ReconfigVM_Task.call_args[0]

        # reconfig_vm_args is a tuple so we need to get the first item which is
        # the vim.VirtualDeviceConfigSpec() and then grab the deviceChange
        observed_device_change_list = reconfig_vm_args[0].deviceChange

        # 2. Check that the deviceChange list is of size 1. We have not updated
        # any more devices.
        self.assertEqual(1, len(observed_device_change_list),
                         "Device Change List must be 1")

        observed_device_change_operation = \
            observed_device_change_list[0].operation

        # 3. Check that the operation is set to edit
        self.assertEqual("add", observed_device_change_operation,
                         "Device Change operation must be edit")

        # Get Device from the change device list
        observed_device_change_device = observed_device_change_list[0].device

        # 1. Check that the backing file name is the same as in the hypervisor
        observed_device_backing_filename = \
            observed_device_change_device.backing.fileName
        self.assertEqual(self.floppy_virtual_media_hypervisor_path,
                         observed_device_backing_filename,
                         "Backing filename is not correct")

        # 2. Check that the backing is ImageBackingInfo.
        # Get the backing from the device
        observed_device_backing = observed_device_change_device.backing
        # The backing is a MagicMock so we need to use the __class__ attribute
        # of the mock to get the spec class. (The class we are mocking)
        # The spec class in this case comes back as
        # pyVmomi.VmomiSupport.LazyType and we cannot check type equality.
        # It is a special wrapper class of pyvimomi for lazy loading and we
        # need to load the real class to get its type.
        # We need to use VmomiSupport.GetVmodlType for this to work. See more
        # info at pyvimomi source code below.
        # https://github.com/vmware/pyvmomi/blob/
        # b9a96ca0c64c4833c23859e998c2e64e0952eac7/pyVmomi/VmomiSupport.py#L206
        observed_device_backing_nonlazy_load_type = VmomiSupport.GetVmodlType(
            observed_device_backing.__class__)

        self.assertEqual(type(vim.vm.device.VirtualFloppy.ImageBackingInfo(
        )), observed_device_backing_nonlazy_load_type,
            "Backing Type is not correct")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_floppy_eject_media(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Mock the _upload_image helper method. There is no real esxi so we
        # cannot upload the iso to a data store.
        self.test_driver._upload_image = mock.MagicMock(
            return_value=self.floppy_virtual_media_hypervisor_path)

        self.test_driver.set_boot_image(
            self.virtual_media_floppy_vm_full_name, "Floppy")

        # Assertions
        # 1. Assert the ReconfigVM_Task method was called on the vm
        vms[self.virtual_media_floppy_vm_full_index].\
            ReconfigVM_Task.assert_called_once()

        # Use call_args to extract the bootOrder from the calling arguments of
        # the mocked vms ReconfigVM_Task method and then check if it contains
        # the expected values.
        # In python38 you can also access .call_args[0] with .call_args.args
        reconfig_vm_args = vms[self.virtual_media_floppy_vm_full_index].\
            ReconfigVM_Task.call_args[0]

        # reconfig_vm_args is a tuple so we need to get the first item which is
        # the vim.VirtualDeviceConfigSpec() and then grab the deviceChange
        observed_device_change_list = reconfig_vm_args[0].deviceChange

        # 2. Check that the deviceChange list is of size 1. We have not updated
        # any more devices.
        self.assertEqual(1, len(observed_device_change_list),
                         "Device Change List must be 1")

        observed_device_change_operation = \
            observed_device_change_list[0].operation

        # 3. Check that the operation is set to remove
        self.assertEqual("remove", observed_device_change_operation,
                         "Device Change operation must be edit")

        # Get Device from the change device list
        observed_device_change_device = observed_device_change_list[0].device

        # 4. Check that we are removing the VirtualFloppy
        self.assertEqual(type(vim.vm.device.VirtualFloppy(
        )), observed_device_change_device.__class__,
            "Device is not vim.vm.device.VirtualFloppy")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_flop_ej_media_cannot_set_boot_dev_ex(self,
                                                                 vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for FISH_ERR_BOOT_IMAGE_CANNOT_BE_SET FishyError
        mock_error_message = ErrMsg.\
            BOOT_IMAGE_CANNOT_BE_SET.format(
                None, "Floppy")
        self.assertRaisesRegex(error.FishyError, mock_error_message,
                               self.test_driver.set_boot_image,
                               self.virtual_media_floppy_vm_empty_name,
                               "Floppy")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_cd_ej_media_cannot_set_boot_device_ex(self,
                                                                  vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for FISH_ERR_BOOT_IMAGE_CANNOT_BE_SET FishyError
        mock_error_message = ErrMsg.\
            BOOT_IMAGE_CANNOT_BE_SET.format(
                None, "Cd")
        self.assertRaisesRegex(error.FishyError, mock_error_message,
                               self.test_driver.set_boot_image,
                               self.virtual_media_cd_non_existent_name, "Cd")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_service_instance_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.set_boot_image,
                               self.virtual_media_cd_non_existent_name, "Cd")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_set_boot_image_invalid_device_type_ex(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for FISH_ERR_INVALID_DEVICE_TYPE FishyError
        error_msg_hdd = ErrMsg.\
            INVALID_DEVICE_TYPE.format("Hdd")
        self.assertRaisesRegex(error.FishyError, error_msg_hdd,
                               self.test_driver.set_boot_image,
                               self.virtual_media_floppy_vm_empty_name, "Hdd")

        error_msg_hdd = ErrMsg.\
            INVALID_DEVICE_TYPE.format("Pxe")
        self.assertRaisesRegex(error.FishyError, error_msg_hdd,
                               self.test_driver.set_boot_image,
                               self.virtual_media_floppy_vm_empty_name, "Pxe")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_simple_storage_collection(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        observed_simple_storage = \
            self.test_driver.get_simple_storage_collection(
                self.disk_controller_vm_name)

        # Assertions
        expected_simple_storage = {
            self.vm_sata_cntl_one_id: {
                'Id': self.vm_sata_cntl_one_id,
                'Name': self.vm_sata_cntl_one_name,
                'DeviceList': [
                    {
                        'Name': self.vm_sata_cntl_one_dev_one_name,
                        'CapacityBytes': self.vm_sata_cntl_one_dev_one_cap_in_b
                    },
                    {
                        'Name': self.vm_sata_cntl_one_dev_two_name,
                        'CapacityBytes': self.vm_sata_cntl_one_dev_two_cap_in_b
                    }
                ]
            },
            self.vm_sata_cntl_two_id: {
                'Id': self.vm_sata_cntl_two_id,
                'Name': self.vm_sata_cntl_two_name,
                'DeviceList': [
                    {
                        'Name': self.vm_sata_cntl_two_dev_one_name,
                        'CapacityBytes': self.vm_sata_cntl_two_dev_one_cap_in_b
                    }
                ]
            },
            self.vm_scsi_cntl_one_id: {
                'Id': self.vm_scsi_cntl_one_id,
                'Name': self.vm_scsi_cntl_one_name,
                'DeviceList': [
                    {
                        'Name': self.vm_scsi_cntl_one_dev_one_name,
                        'CapacityBytes': self.vm_scsi_cntl_one_dev_one_cap_in_b
                    }
                ]
            }
        }

        self.assertEqual(expected_simple_storage,
                         observed_simple_storage,
                         "Incorect simple storage output")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_simple_storage_collection_empty(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        observed_simple_storage = \
            self.test_driver.get_simple_storage_collection(
                self.disk_controller_empty_vm_name)

        # Assertions
        expected_simple_storage = {}

        self.assertEqual(expected_simple_storage,
                         observed_simple_storage,
                         "Incorect simple storage output")

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_get_simple_storage_collection_service_instance_ex(self,
                                                               vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, True)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        mock_error_message = FakeException.VMWARE_OPEN_MOCK_EXCEPTION.value
        self.assertRaisesRegex(Exception, mock_error_message,
                               self.test_driver.get_simple_storage_collection,
                               self.disk_controller_vm_name)

    @mock.patch(
        'sushy_tools.emulator.resources.systems.vmwaredriver.VmwareOpen')
    def test_find_or_create_storage_volume(self, vmware_mock):

        # Setup Mocked Service Instance
        si, vms = self.setup_service_instance(self.fake_vms, False)

        # Get the VmwareOpen reference
        vm_mock = vmware_mock.return_value
        # Mock the enter and exit methods
        vm_mock.__enter__ = mock.MagicMock(return_value=si)
        vm_mock.__exit__ = mock.MagicMock(return_value=None)

        # Assert for Service Instance Error
        error_message = 'Not implemented'
        self.assertRaisesRegex(error.NotSupportedError, error_message,
                               self.test_driver.find_or_create_storage_volume,
                               None)
