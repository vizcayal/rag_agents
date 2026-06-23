import boto3
import time
import sys
from botocore.exceptions import ClientError

REGION = "us-east-1"
ROLE_NAME = "AgentCore-BedrockKB-Role"
POLICY_NAME = "AgentCore-BedrockKB-Policy"
COLLECTION_NAME = "rag-agent-kb"
COLLECTION_GROUP_NAME = "rag-agent-kb-group"
KB_NAME = "EU-AI-Act-KB"

iam = boto3.client("iam", region_name=REGION)
aoss = boto3.client("opensearchserverless", region_name=REGION)
bedrock_agent = boto3.client("bedrock-agent", region_name=REGION)

def delete_knowledge_base():
    print(f"Checking for Bedrock Knowledge Base: {KB_NAME}...")
    kb_id = None
    try:
        kbs = bedrock_agent.list_knowledge_bases(maxResults=50)
        for kb in kbs.get("knowledgeBaseSummaries", []):
            if kb["name"] == KB_NAME:
                kb_id = kb["knowledgeBaseId"]
                break
    except Exception as e:
        print(f"Error listing Knowledge Bases: {e}")
        return

    if not kb_id:
        print(f"Knowledge Base '{KB_NAME}' not found. Skipping.")
        return

    print(f"Found Knowledge Base ID: {kb_id}")
    
    # 1. List and delete data sources
    try:
        dss = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)
        for ds in dss.get("dataSourceSummaries", []):
            ds_id = ds["dataSourceId"]
            print(f"Deleting Data Source: {ds_id}...")
            try:
                ds_detail = bedrock_agent.get_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)["dataSource"]
                if ds_detail.get("dataDeletionPolicy") != "RETAIN":
                    print("Updating data deletion policy to RETAIN to prevent dependency failure on deleted vector store...")
                    bedrock_agent.update_data_source(
                        knowledgeBaseId=kb_id,
                        dataSourceId=ds_id,
                        name=ds_detail["name"],
                        dataDeletionPolicy="RETAIN",
                        dataSourceConfiguration=ds_detail["dataSourceConfiguration"],
                        description=ds_detail.get("description", "")
                    )
            except Exception as ex:
                print(f"Warning: Failed to retrieve or update data source details: {ex}")
            
            bedrock_agent.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)
            print("Data Source deleted.")
    except Exception as e:
        print(f"Error deleting data sources: {e}")
        
    # 2. Delete Knowledge Base
    try:
        print(f"Deleting Knowledge Base {kb_id}...")
        bedrock_agent.delete_knowledge_base(knowledgeBaseId=kb_id)
        print("✅ Knowledge Base deleted successfully.")
    except Exception as e:
        print(f"Error deleting Knowledge Base: {e}")

def delete_aoss_collection():
    print(f"Checking for OpenSearch Serverless collection: {COLLECTION_NAME}...")
    collection_id = None
    try:
        collections = aoss.list_collections(
            collectionFilters={"name": COLLECTION_NAME}
        )
        summaries = collections.get("collectionSummaries", [])
        if summaries:
            collection_id = summaries[0]["id"]
    except Exception as e:
        print(f"Error listing collections: {e}")
        return

    if not collection_id:
        print(f"Collection '{COLLECTION_NAME}' not found. Skipping.")
    else:
        print(f"Found Collection ID: {collection_id}. Deleting...")
        try:
            aoss.delete_collection(id=collection_id)
            print(f"Collection deletion initiated. ID: {collection_id}")
            
            # Wait for collection deletion
            while True:
                try:
                    res = aoss.batch_get_collection(ids=[collection_id])
                    details = res.get("collectionDetails", [])
                    if not details:
                        print("✅ Collection successfully deleted.")
                        break
                    status = details[0]["status"]
                    print(f"Collection status: {status}...")
                    if status == "DELETING":
                        pass
                    else:
                        print(f"Unexpected status: {status}")
                except ClientError as e:
                    # If not found or access error, it's deleted
                    print("✅ Collection successfully deleted.")
                    break
                except Exception as e:
                    print(f"Collection check: {e}")
                    break
                time.sleep(10)
        except Exception as e:
            print(f"Error deleting collection: {e}")

    # Delete security and access policies
    policies = [
        (f"{COLLECTION_NAME}-enc", "encryption"),
        (f"{COLLECTION_NAME}-net", "network"),
        (f"{COLLECTION_NAME}-access", "data")
    ]
    
    for name, p_type in policies:
        print(f"Deleting security policy: {name} (type: {p_type})...")
        try:
            if p_type == "data":
                aoss.delete_access_policy(name=name, type=p_type)
            else:
                aoss.delete_security_policy(name=name, type=p_type)
            print(f"✅ Deleted policy: {name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                print(f"Policy {name} not found. Skipping.")
            else:
                print(f"Error deleting policy {name}: {e}")
        except Exception as e:
            print(f"Error deleting policy {name}: {e}")

def delete_iam_role():
    print(f"Checking for IAM Role: {ROLE_NAME}...")
    try:
        iam.get_role(RoleName=ROLE_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            print(f"Role '{ROLE_NAME}' not found. Skipping.")
            return
        else:
            print(f"Error fetching role: {e}")
            return
            
    # Delete inline policies
    try:
        print(f"Deleting inline policy {POLICY_NAME} from role {ROLE_NAME}...")
        iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=POLICY_NAME)
        print("Policy deleted.")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            print("Inline policy not found.")
        else:
            print(f"Error deleting inline policy: {e}")
            
    # Delete Role
    try:
        print(f"Deleting Role {ROLE_NAME}...")
        iam.delete_role(RoleName=ROLE_NAME)
        print("✅ IAM Role deleted successfully.")
    except Exception as e:
        print(f"Error deleting Role: {e}")

def delete_aoss_collection_group():
    print(f"Checking for OpenSearch Serverless collection group: {COLLECTION_GROUP_NAME}...")
    try:
        groups = aoss.list_collection_groups()
        group_id = None
        for g in groups.get("collectionGroupSummaries", []):
            if g["name"] == COLLECTION_GROUP_NAME:
                group_id = g["id"]
                break
        
        if not group_id:
            print(f"Collection group '{COLLECTION_GROUP_NAME}' not found. Skipping.")
            return

        print(f"Found Collection Group ID: {group_id}. Deleting...")
        aoss.delete_collection_group(id=group_id)
        print("✅ Collection group deleted successfully.")
    except Exception as e:
        print(f"Error deleting collection group: {e}")

def main():
    print("=== Teardown / Deletion of RAG KB Resources ===")
    delete_knowledge_base()
    delete_aoss_collection()
    delete_aoss_collection_group()
    delete_iam_role()
    print("=== Teardown complete ===")

if __name__ == "__main__":
    main()
