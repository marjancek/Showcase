import subprocess
from novaclient.v1_1 import client as novaclient
import time
import paramiko
import select
from scripts.common.Cloudscale import *
from webservice.settings import BASE_DIR


class CreateInstance:

    def __init__(self, config, logger):
        self.mvn_path = '/usr/bin/mvn'
        self.cfg = config.cfg
        self.config = config
        self.logger = logger

        self.user = self.cfg.get('OPENSTACK', 'username')
        self.pwd = self.cfg.get('OPENSTACK', 'password')
        self.url = self.cfg.get('OPENSTACK', 'auth_url')
        self.tenant = self.cfg.get('OPENSTACK', 'tenant_name')

        self.image_name = self.cfg.get('OPENSTACK', 'image_name')

        self.instance_type = self.cfg.get('OPENSTACK', 'instance_type')
        self.instance_name = 'cloudscale-sc'

        self.key_name = self.cfg.get('OPENSTACK', 'key_name')
        self.key_pair = self.cfg.get('OPENSTACK', 'key_pair')

        self.showcase_image_name = "cloudscale-sc-image"

        self.database_name = self.cfg.get('MYSQL', 'database_name')
        self.database_user = self.cfg.get('MYSQL', 'database_user')
        self.database_pass = self.cfg.get('MYSQL', 'database_pass')

        self.connection_pool_size = self.cfg.get('MYSQL', 'connection_pool_size')

        self.database_type = self.cfg.get('OPENSTACK', 'database_type').lower()
        if self.database_type != 'mysql':
            self.showcase_image_name = "cloudscale-sc-mongo-image"

            self.database_name = self.cfg.get('MONGODB', 'database_name')
            self.database_user = self.cfg.get('MONGODB', 'database_user')
            self.database_pass = self.cfg.get('MONGODB', 'database_pass')


        self.remote_user = self.cfg.get('OPENSTACK', 'image_username')

        self.num_instances = self.config.fr.get('num_instances')

        self.nc = novaclient.Client(self.user, self.pwd, self.tenant, auth_url=self.url)
        self.delete_image(self.showcase_image_name)
        self.logger.log("Creating showcase instance image:")
        images = self.nc.images.list()
        for image in images:
            if image.name == self.showcase_image_name:
                self.logger.log('Image already exists.')
                break
        else:
            self.file_path = BASE_DIR + "/scripts/software"

            userdata = """#!/bin/bash
USERNAME=%s
""" % self.remote_user + open(self.file_path + '/install-apache-tomcat.sh', 'r').read()
            server_id = self.create_instance()
            server_floating_ip = self.add_floating_ip(server_id)

            self.add_security_group(server_id, "ssh")

            self.create_showcase_database_config()
            self.install_software(server_floating_ip, userdata)
            self.upload_configs(server_floating_ip)

            self.compile()

            self.deploy_showcase(server_floating_ip)

            self.wait_powered_off(server_floating_ip, server_id)
            self.create_image(server_id, self.showcase_image_name)
            self.delete_instance(server_id)

            self.logger.log('Done creating showcase instance image')

        self.logger.log('Creating showcase instances')
        self.create_showcase_instances()
        self.logger.log('Done creating showcase instances')

    def install_software(self, ip, user_data):
        self.logger.log('Installing apache and tomcat ...')
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        while True:
            try:
                ssh.connect(ip, username=self.remote_user, key_filename=os.path.abspath(self.key_pair))
                break
            except:
                time.sleep(5)
        time.sleep(30)
        scp = paramiko.SFTPClient.from_transport(ssh.get_transport())
        scp.put(self.file_path + '/install-apache-tomcat.sh', 'setup.sh')
        _, stdout, _ = ssh.exec_command("sh setup.sh")
        self.wait_for_command(stdout, verbose=True)

    def upload_configs(self, ip):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        while True:
            try:
                ssh.connect(ip, username=self.remote_user, key_filename=os.path.abspath(self.key_pair))
                break
            except:
                time.sleep(5)

        scp = paramiko.SFTPClient.from_transport(ssh.get_transport())
        self.logger.log("Uploading configs on %s" % ip)
        scp.put(self.file_path + '/../platform/server.xml', 'server.xml')
        scp.put(self.file_path + '/cloudscale-apache-virtualhost.conf', 'cloudscale-apache-virtualhost.conf')
        scp.put(self.file_path + '/../platform/mpm_worker.conf', 'mpm_worker.conf')

        _, stdout, _ = ssh.exec_command("sudo mv server.xml /var/lib/tomcat7/conf/server.xml")
        self.wait_for_command(stdout, verbose=True)

        _, stdout, _ = ssh.exec_command("sudo mv cloudscale-apache-virtualhost.conf /etc/apache2/sites-available/cloudscale")
        self.wait_for_command(stdout, verbose=True)

        _, stdout, _ = ssh.exec_command("sudo mv mpm_worker.conf /etc/apache2/mods-enabled/mpm_worker.conf")
        self.wait_for_command(stdout, verbose=True)

    def wait_for_command(self, stdout, verbose=False):
        # Wait for the command to terminate
        while not stdout.channel.exit_status_ready():
        # Only print data if there is data to read in the channel
            if stdout.channel.recv_ready():
                rl, wl, xl = select.select([stdout.channel], [], [], 0.0)
                if len(rl) > 0:
                    response = stdout.channel.recv(1024)
                    if verbose:
                        print response

    def delete_image(self, image_name):
        try:
            for image in self.nc.images.list():
                if image.name == image_name:
                    image.delete()
                    break
        except Exception as e:
            raise e

    def delete_instance(self, server_id):
        self.logger.log("Deleting instance ...")
        server = self.nc.servers.get(server_id)
        server.delete()

    def create_instance(self, image_name=None, files=None, userdata=None, wait_on_active_status=True):
        if image_name is None:
            image_name = self.image_name

        for f in self.nc.flavors.list():
            if f.name == self.instance_type:
                flavor = f
                break
        else:
            self.logger.log("Instance flavor '%s' not found!" % self.instance_type)
            return False

        for img in self.nc.images.list():
            if img.name == image_name:
                image = img
                break
        else:
            self.logger.log("Image '%s' not found!" % image_name)
            return False

        server_id = self.nc.servers.create(
            self.instance_name, image, flavor, key_name=self.key_name, files=files, userdata=userdata
        ).id

        if wait_on_active_status and not self.wait_active(server_id):
            return False

        return server_id

    def wait_active(self, server_id):
        self.logger.log("Waiting for instance to be built . . .")
        status = self.wait_for_instance_status(server_id, u'BUILD', u'ACTIVE')
        if not status:
            self.logger.log("Can not start instance %s!" % self.instance_name)
            return False
        return True

    def wait_all_instances_active(self, instance_ids):
        for instance_id in instance_ids:
            self.wait_active(instance_id)

    def wait_powered_off(self, ip, server_id):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip, username=self.remote_user, key_filename=os.path.abspath(self.key_pair))
        _, stdout, _ = ssh.exec_command("sudo poweroff")
        self.wait_for_command(stdout, verbose=True)

        self.logger.log("Waiting for instance %s to be powered off . . ." % server_id)
        status = self.wait_for_instance_status(server_id, u'ACTIVE', u'SHUTOFF')
        if not status:
            self.logger.log("Error on instance %s!" % self.instance_name)
            return False
        return True

    def wait_for_instance_status(self, server_id, current_status, wait_for_status):
        while True:
            server = self.nc.servers.get(server_id)
            if server.status != current_status:
                if server.status == wait_for_status:
                    return True
                return False
            time.sleep(1)

    def create_image(self, server_id, image_name):
        self.logger.log("Creating image ...")
        server = self.nc.servers.get(server_id)
        image_id = server.create_image(image_name)

        while True:
            image = self.nc.images.get(image_id)
            if image.status == u'ACTIVE':
                return True
            if image.status == u'ERROR':
                self.logger.log("Error creating image!")
                return False
            time.sleep(3)

    def compile(self):
        self.logger.log("Compiling showcase ...")
        if self.database_type == "mysql":
            ps = subprocess.Popen('cd ' + self.file_path + '/showcase; '+ self.mvn_path + ' -Pamazon-hibernate -Dconnection_pool_size='
                                        + self.connection_pool_size + ' install',
                                  shell=True)
            ps.wait()
            if ps.returncode != 0:
                self.logger.log("ERROR: showcase failed to compile!")
                pass
        else:
            ps = subprocess.Popen('cd ' + self.file_path + '/showcase; ' + self.mvn_path + ' -Pamazon-mongodb -Dconnection_pool_size='
                                        + self.connection_pool_size + ' install',
                                  shell=True)
            ps.wait()
            if ps.returncode != 0:
                self.logger.log("ERROR: showcase failed to compile!")
        pass

    def create_showcase_instances(self):
        showcase_server_ids = []

        for i in range(int(self.num_instances)):
            self.logger.log("Creating showcase instance %s ..." % (i + 1))
            showcase_server_ids.append(
                self.create_instance(image_name=self.showcase_image_name, wait_on_active_status=False)
            )

        self.wait_all_instances_active(showcase_server_ids)

        for server_id in showcase_server_ids:
            self.add_security_group(server_id, 'http')

    def add_floating_ip(self, server_id):
        server = self.nc.servers.get(server_id)
        unallocated_floating_ips = self.nc.floating_ips.findall(fixed_ip=None)
        if len(unallocated_floating_ips) < 1:
            unallocated_floating_ips.append(self.nc.floating_ips.create())
        floating_ip = unallocated_floating_ips[0]
        server.add_floating_ip(floating_ip)
        return floating_ip.ip

    def add_security_group(self, server_id, group_name):
        self.logger.log("Adding security group %s" % group_name)
        server = self.nc.servers.get(server_id)
        server.add_security_group(group_name)

    def deploy_showcase(self, ip_address):
        self.logger.log("Connecting to ssh on %s" % ip_address)
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        while True:
            try:
                ssh.connect(ip_address, username=self.remote_user, key_filename=os.path.abspath(self.key_pair))
                break
            except:
                time.sleep(5)

        scp = paramiko.SFTPClient.from_transport(ssh.get_transport())
        self.logger.log("Uploading showcase.war")
        scp.put(self.file_path + '/showcase/target/showcase-1.0.0-BUILD-SNAPSHOT.war', 'showcase-1-a.war')
        _, stdout, _ = ssh.exec_command("sudo mv showcase-1-a.war /var/lib/tomcat7/webapps")
        self.wait_for_command(stdout, verbose=True)
        ssh.exec_command("sudo chown tomcat7:tomcat7 /var/lib/tomcat7/webapps/showcase-1-a.war")

    def get_ip(self, server):
        for address in server.addresses[server.addresses.keys()[0]]:
            if address['OS-EXT-IPS:type'] == 'fixed':
                server_ip = address['addr']
                break
        else:
            server_ip = None
            self.logger.log("Error: can not get IP address of this server")
        return server_ip

    def create_showcase_database_config(self):
        if self.database_type == 'mysql':
            self.logger.log("Creating jdbc configuration")
            path = self.file_path + '/showcase/src/main/resources/database/database.aws.hibernate.properties'

            servers = self.nc.servers.findall(name='cloudscale-db')
            ips = []
            for server in servers:
                server_ip = self.get_ip(server)
                ips.append(server_ip)
            urls = ','.join(ips)

            f = open(path, 'w')
            f.write('jdbc.dbtype=mysql\n')
            f.write('jdbc.driverClassName=com.mysql.jdbc.Driver\n')
            f.write('jdbc.url=jdbc:mysql:loadbalance://%s/%s\n' % (urls, self.database_name))
            f.write('jdbc.username=%s\n' % self.database_user)
            f.write('jdbc.password=%s\n' % self.database_pass)
            f.write('jdbc.hibernate.dialect=org.hibernate.dialect.MySQLDialect\n')
            f.close()
        else:
            self.logger.log("Creating jdbc configuration")
            path = self.file_path + '/showcase/src/main/resources/database/database.aws.mongodb.properties'

            servers = self.nc.servers.findall(name='cloudscale-db-mongo')
            ips = []
            for server in servers:
                server_ip = self.get_ip(server)
                ips.append(server_ip)
            urls = ','.join(ips)

            f = open(path, 'w')
            f.write('jdbc.dbtype=mongodb\n')
            f.write('mongodb.dbname=tpcw\n')
            f.write('mongodb.host=%s\n' % ips[0])
            f.write('mongodb.port=27017\n')
            f.write('mongodb.username=tpcw\n')
            f.write('mongodb.password=Yhm.3Ub+\n')
            f.close()


if __name__ == '__main__':
    check_args(1, "<config_path>")
    _, cfg, key_name, key_pair = parse_args('OPENSTACK')
    CreateInstance(cfg, key_name, key_pair)
