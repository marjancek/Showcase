from novaclient.exceptions import NotFound, BadRequest, NoUniqueMatch
from scripts.common.Cloudscale import *
import time

from novaclient.v1_1 import client as novaclient


class RemoveAll:
    def __init__(self, config, logger):
        self.cfg = config.cfg
        self.config = config
        self.logger = logger

        self.user = self.cfg.get('OPENSTACK', 'username')
        self.pwd = self.cfg.get('OPENSTACK', 'password')
        self.url = self.cfg.get('OPENSTACK', 'auth_url')
        self.tenant = self.cfg.get('OPENSTACK', 'tenant_name')

        self.image_name = self.cfg.get('OPENSTACK', 'image_name')
        self.instance_type = self.cfg.get('OPENSTACK', 'instance_type')
        self.instance_name = 'cloudscale'
        self.key_name = self.cfg.get('OPENSTACK', 'key_name')
        self.key_pair = self.cfg.get('OPENSTACK', 'key_pair')

        self.nc = novaclient.Client(self.user, self.pwd, self.tenant, auth_url=self.url)

        self.logger.log("Cleaning:")
        self.remove_openstack_instances()
        # TODO: remove images
        self.remove_key_pair()
        self.remove_security_groups()
        self.logger.log("Done cleaning")

        self.logger.log('Creating security groups and key-pair:')
        self.create_security_groups()
        self.create_key_pair()
        self.logger.log('Done creating security groups and key-pair')

    def remove_key_pair(self):
        self.logger.log("Removing key pair ...")
        try:
            self.nc.keypairs.find(name=self.key_name).delete()
        except NotFound as e:
            pass

    def remove_openstack_instances(self):
        self.logger.log("Terminating openstack instances ...")

        instance_ids = []
        for server in self.nc.servers.list():
            if server.name in ('cloudscale-db', 'cloudscale-sc', 'cloudscale-lb', 'cloudscale-mc'):
                instance_ids.append(server.id)
                server.delete()

        for instance_id in instance_ids:
            self.wait_terminate(instance_id)

    def wait_terminate(self, instance_id):
        self.logger.log("Waiting for instance to terminate, please wait ...")
        while True:
            try:
                server = self.nc.servers.get(instance_id)
            except NotFound as e:
                break
            time.sleep(3)
        self.logger.log("Instance is terminated!")

    def remove_security_groups(self):
        self.logger.log("Removing security groups ...")

        groups = ['ssh', 'http', 'galera', 'mongodb', 'web-deploy']
        for group in groups:
            try:
                self.nc.security_groups.find(name=group).delete()
            except (NoUniqueMatch, NotFound, BadRequest):
                pass
            except Exception as exc:
                pass

    def create_key_pair(self):
        self.logger.log("Creating key pair ...")
        if os.path.isfile(self.key_pair):
            if not os.path.isfile(self.key_pair + '.pub'):
                self.logger.log("Can not find public key %s!" % (self.key_pair + '.pub'))
                exit(1)
            public_key = open(self.key_pair + '.pub', 'r').read()
            self.nc.keypairs.create(self.key_name, public_key=public_key)
        else:
            try:
                keypair = self.nc.keypairs.find(name=self.key_name)
            except NotFound as e:
                keypair = self.nc.keypairs.create(self.key_name)

            open(self.key_pair + '.pub', 'w').write(keypair.public_key)
            open(self.key_pair, 'w').write(keypair.private_key)

    def create_security_groups(self):
        self.logger.log("Creating security groups http and ssh ...")
        self.create_security_group('http', 'Security group for HTTP protocol', 'tcp', '80', '0.0.0.0/0')
        self.create_security_group('ssh', 'Security group for SSH protocol', 'tcp', '22', '0.0.0.0/0')
        self.create_security_group('galera', 'Security group for MySQL galera protocol', 'tcp',
                                   ['3306', '4444', '4567', '4568'], '0.0.0.0/0')
        self.create_security_group('mongo', 'Security group for MongoDB protocol', 'tcp',
                                   ['27017', '27037', '27019'], '0.0.0.0/0')

    def create_security_group(self, name, description, protocol,  ports, cidr):
        try:
            security_group = self.nc.security_groups.find(name=name)
        except NotFound:
            security_group = self.nc.security_groups.create(name, description)
        except Exception as e:
            raise e

        try:
            if not isinstance(ports, list):
                ports = [ports]
            for port in ports:
                self.nc.security_group_rules.create(security_group.id, protocol, port, port, cidr)
        except BadRequest as e:
            if e.message != u'Security group %s already exists' % name and e.message != u'This rule already exists in group %s' % security_group.id:
                raise
        except Exception as e:
            pass

if __name__ == "__main__":
    check_args(1, "<config_path>")
    _, cfg, key_name, key_pair = parse_args('OPENSTACK')

    RemoveAll(cfg, key_name, key_pair)
