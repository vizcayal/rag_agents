import boto3
import sys

REGION = "us-east-1"

def find_cluster_and_service(ecs):
    # Find cluster
    clusters = ecs.list_clusters().get("clusterArns", [])
    target_cluster = None
    for c in clusters:
        if "AgentCore-ragAgent-default-UiCluster" in c:
            target_cluster = c.split("/")[-1]
            break
            
    if not target_cluster:
        print("Could not find matching ECS Cluster.")
        return None, None
        
    # Find service in cluster
    services = ecs.list_services(cluster=target_cluster).get("serviceArns", [])
    target_service = None
    for s in services:
        if "StreamlitService" in s:
            target_service = s.split("/")[-1]
            break
            
    if not target_service:
        print("Could not find matching Streamlit service in cluster.")
        return None, None
        
    return target_cluster, target_service

def get_fargate_task_ip():
    ecs = boto3.client("ecs", region_name=REGION)
    ec2 = boto3.client("ec2", region_name=REGION)
    
    cluster_name, service_name = find_cluster_and_service(ecs)
    if not cluster_name or not service_name:
        return None
        
    # 1. List active tasks
    try:
        response = ecs.list_tasks(cluster=cluster_name, serviceName=service_name)
        task_arns = response.get("taskArns", [])
        if not task_arns:
            print("No tasks found running for the service.")
            return None
        task_arn = task_arns[0]
    except Exception as e:
        print(f"Error listing tasks: {e}")
        return None
        
    # 2. Describe task to get the network interface details
    try:
        desc = ecs.describe_tasks(cluster=cluster_name, tasks=[task_arn])
        tasks = desc.get("tasks", [])
        if not tasks:
            print("Failed to describe the task details.")
            return None
        task = tasks[0]
        
        # Look for ENI attachment
        eni_id = None
        for attachment in task.get("attachments", []):
            if attachment.get("type") == "ElasticNetworkInterface":
                for detail in attachment.get("details", []):
                    if detail.get("name") == "networkInterfaceId":
                        eni_id = detail.get("value")
                        break
            if eni_id:
                break
        
        if not eni_id:
            print("Could not find Elastic Network Interface (ENI) ID for the Fargate task.")
            return None
            
    except Exception as e:
        print(f"Error describing tasks: {e}")
        return None
        
    # 3. Query EC2 to get the public IP of the ENI
    try:
        network_interfaces = ec2.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
        interfaces = network_interfaces.get("NetworkInterfaces", [])
        if not interfaces:
            print("Network interface details not returned by EC2.")
            return None
        
        interface = interfaces[0]
        association = interface.get("Association", {})
        public_ip = association.get("PublicIp")
        
        if not public_ip:
            # Maybe the task is still launching or provisioning the IP
            print("Fargate task has no public IP associated yet. Wait a moment and run this script again.")
            return None
            
        return public_ip
        
    except Exception as e:
        print(f"Error describing network interface {eni_id}: {e}")
        return None

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
    ip = get_fargate_task_ip()
    if ip:
        print("\n========================================================")
        print("🎉 STREAMLIT APP DEPLOYED DIRECTLY (NO LOAD BALANCER) 🎉")
        print(f"Access URL: http://{ip}:8501")
        print("========================================================\n")
    else:
        sys.exit(1)
