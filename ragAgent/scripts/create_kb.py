import boto3
import json
import time
import sys
import os
import urllib.parse
import hashlib
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.endpoint import URLLib3Session
from botocore.exceptions import ClientError

REGION = "us-east-1"
ROLE_NAME = "AgentCore-BedrockKB-Role"
POLICY_NAME = "AgentCore-BedrockKB-Policy"
COLLECTION_NAME = "rag-agent-kb"
COLLECTION_GROUP_NAME = "rag-agent-kb-group"
INDEX_NAME = "bedrock-knowledge-base-index"
KB_NAME = "EU-AI-Act-KB"
S3_BUCKET = "rag-agents-docs"

iam = boto3.client("iam", region_name=REGION)
aoss = boto3.client("opensearchserverless", region_name=REGION)
bedrock_agent = boto3.client("bedrock-agent", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)

def get_current_user_arn():
    res = sts.get_caller_identity()
    return res["Arn"]

def create_or_get_role():
    print(f"Creating or getting IAM role: {ROLE_NAME}...")
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "bedrock.amazonaws.com"
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }
    
    try:
        res = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role for Bedrock Knowledge Base access to S3 and OpenSearch Serverless"
        )
        role_arn = res["Role"]["Arn"]
        print(f"Created new role: {role_arn}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            res = iam.get_role(RoleName=ROLE_NAME)
            role_arn = res["Role"]["Arn"]
            print(f"Role already exists: {role_arn}")
        else:
            raise e
            
    # Attach Policy
    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:ListBucket"
                ],
                "Resource": [
                    f"arn:aws:s3:::{S3_BUCKET}",
                    f"arn:aws:s3:::{S3_BUCKET}/*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel"
                ],
                "Resource": [
                    "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "aoss:APIAccessAll"
                ],
                "Resource": [
                    f"arn:aws:aoss:us-east-1:*:collection/*"
                ]
            }
        ]
    }
    
    try:
        iam.put_role_policy(
            RoleName=ROLE_NAME,
            PolicyName=POLICY_NAME,
            PolicyDocument=json.dumps(policy_doc)
        )
        print("Attached inline permissions policy to role.")
    except Exception as e:
        print(f"Warning attaching policy: {e}")
        
    # Wait a bit for IAM propagation
    time.sleep(5)
    return role_arn

def create_aoss_policies(user_arn, role_arn):
    print("Configuring OpenSearch Serverless security policies...")
    
    # 1. Encryption Policy
    enc_policy_name = f"{COLLECTION_NAME}-enc"
    enc_policy = {
        "Rules": [
            {
                "ResourceType": "collection",
                "Resource": [f"collection/{COLLECTION_NAME}"]
            }
        ],
        "AWSOwnedKey": True
    }
    try:
        aoss.create_security_policy(
            name=enc_policy_name,
            type="encryption",
            policy=json.dumps(enc_policy)
        )
        print(f"Created encryption policy: {enc_policy_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            print(f"Encryption policy {enc_policy_name} already exists.")
        else:
            raise e

    # 2. Network Policy
    net_policy_name = f"{COLLECTION_NAME}-net"
    net_policy = [
        {
            "Rules": [
                {
                    "ResourceType": "collection",
                    "Resource": [f"collection/{COLLECTION_NAME}"]
                },
                {
                    "ResourceType": "dashboard",
                    "Resource": [f"collection/{COLLECTION_NAME}"]
                }
            ],
            "AllowFromPublic": True
        }
    ]
    try:
        aoss.create_security_policy(
            name=net_policy_name,
            type="network",
            policy=json.dumps(net_policy)
        )
        print(f"Created network policy: {net_policy_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            print(f"Network policy {net_policy_name} already exists.")
        else:
            raise e

    # 3. Data Access Policy
    access_policy_name = f"{COLLECTION_NAME}-access"
    access_policy = [
        {
            "Rules": [
                {
                    "ResourceType": "collection",
                    "Resource": ["collection/*"],
                    "Permission": [
                        "aoss:CreateCollectionItems",
                        "aoss:DeleteCollectionItems",
                        "aoss:UpdateCollectionItems",
                        "aoss:DescribeCollectionItems"
                    ]
                },
                {
                    "ResourceType": "index",
                    "Resource": ["index/*/*"],
                    "Permission": [
                        "aoss:CreateIndex",
                        "aoss:DeleteIndex",
                        "aoss:UpdateIndex",
                        "aoss:DescribeIndex",
                        "aoss:ReadDocument",
                        "aoss:WriteDocument"
                    ]
                }
            ],
            "Principal": [user_arn, role_arn]
        }
    ]
    try:
        aoss.create_access_policy(
            name=access_policy_name,
            type="data",
            policy=json.dumps(access_policy)
        )
        print(f"Created data access policy: {access_policy_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            # If exists, we should update it to make sure the principals are correct
            try:
                curr = aoss.get_access_policy(name=access_policy_name, type="data")
                version = curr["accessPolicyDetail"]["policyVersion"]
                aoss.update_access_policy(
                    name=access_policy_name,
                    type="data",
                    policyVersion=version,
                    policy=json.dumps(access_policy)
                )
                print(f"Updated data access policy to use wildcards (version {version}): {access_policy_name}")
            except Exception as update_err:
                print(f"Access policy update failed: {update_err}")
        else:
            raise e
def create_or_get_collection_group(group_name):
    print(f"Checking for OpenSearch Serverless collection group: {group_name}...")
    try:
        groups = aoss.list_collection_groups()
        for g in groups.get("collectionGroupSummaries", []):
            if g["name"] == group_name:
                print(f"Collection group '{group_name}' already exists.")
                return group_name
    except Exception as e:
        print(f"Warning listing collection groups: {e}")

    print(f"Creating NextGen collection group '{group_name}' with standbyReplicas='DISABLED' (Cheapest options)...")
    try:
        res = aoss.create_collection_group(
            name=group_name,
            standbyReplicas="DISABLED",
            capacityLimits={
                "maxIndexingCapacityInOCU": 2,
                "maxSearchCapacityInOCU": 2
            }
        )
        print(f"Collection group created successfully: {res['createCollectionGroupDetail']['id']}")
        return group_name
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            print(f"Collection group '{group_name}' already exists (conflict).")
            return group_name
        else:
            raise e

def create_aoss_collection():
    create_or_get_collection_group(COLLECTION_GROUP_NAME)
    print(f"Creating OpenSearch Serverless collection: {COLLECTION_NAME}...")
    try:
        res = aoss.create_collection(
            name=COLLECTION_NAME,
            type="VECTORSEARCH",
            collectionGroupName=COLLECTION_GROUP_NAME,
            description="Vector collection for Bedrock Knowledge Base"
        )
        collection_id = res["createCollectionDetail"]["id"]
        print(f"Collection creation initiated. ID: {collection_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            # Collection already exists, find ID
            collections = aoss.list_collections(
                collectionFilters={"name": COLLECTION_NAME}
            )
            collection_id = collections["collectionSummaries"][0]["id"]
            print(f"Collection already exists. ID: {collection_id}")
        else:
            raise e
            
    # Poll for ACTIVE status
    while True:
        res = aoss.batch_get_collection(ids=[collection_id])
        detail = res["collectionDetails"][0]
        status = detail["status"]
        print(f"Collection status: {status}...")
        if status == "ACTIVE":
            endpoint = detail["collectionEndpoint"]
            print(f"Collection is ACTIVE. Endpoint: {endpoint}")
            return collection_id, endpoint
        elif status == "FAILED":
            raise RuntimeError("Collection creation failed!")
        time.sleep(15)

def create_vector_index(endpoint):
    print(f"Checking vector index '{INDEX_NAME}' in OpenSearch Serverless...")
    # Clean endpoint format
    if endpoint.startswith("https://"):
        endpoint = endpoint
    else:
        endpoint = f"https://{endpoint}"
        
    url = f"{endpoint}/{INDEX_NAME}"
    parsed_url = urllib.parse.urlparse(url)
    session = boto3.Session()
    credentials = session.get_credentials()
    urllib_session = URLLib3Session()

    # 1. Check if index already exists
    print("Checking if index already exists...")
    check_request = AWSRequest(
        method="GET",
        url=url,
        headers={"host": parsed_url.netloc}
    )
    SigV4Auth(credentials, "aoss", REGION).add_auth(check_request)
    check_prep = check_request.prepare()
    check_res = urllib_session.send(check_prep)
    
    if check_res.status_code == 200:
        print("Vector index already exists. Skipping creation.")
        return
        
    print(f"Index check status: {check_res.status_code}. Recreating...")

    # 2. Create index with correct settings and mapping
    print("Creating vector index with mapping...")
    payload = {
        "settings": {
            "index.knn": True
        },
        "mappings": {
            "properties": {
                "AMAZON_BEDROCK_METADATA": {
                    "type": "text",
                    "index": False
                },
                "AMAZON_BEDROCK_TEXT_CHUNK": {
                    "type": "text"
                },
                "bedrock-vector": {
                    "type": "knn_vector",
                    "dimension": 1536,
                    "method": {
                        "name": "hnsw",
                        "engine": "faiss",
                        "space_type": "l2"
                    }
                }
            }
        }
    }
    
    data_bytes = json.dumps(payload).encode("utf-8")
    payload_hash = hashlib.sha256(data_bytes).hexdigest()
    
    request = AWSRequest(
        method="PUT",
        url=url,
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "host": parsed_url.netloc,
            "x-amz-content-sha256": payload_hash
        }
    )
    SigV4Auth(credentials, "aoss", REGION).add_auth(request)
    prep = request.prepare()
    
    # Try index creation with exponential backoff in case OpenSearch security policy isn't fully propagated yet
    for attempt in range(5):
        try:
            response = urllib_session.send(prep)
            if response.status_code in (200, 201):
                print(f"Successfully created OpenSearch index. HTTP {response.status_code}")
                print("Waiting 30 seconds for index metadata and permissions to propagate...")
                time.sleep(30)
                return
            elif response.status_code == 400 and "resource_already_exists_exception" in response.text:
                print("Index already exists.")
                return
            else:
                print(f"Attempt {attempt+1}: Failed to create index. HTTP {response.status_code}: {response.text}")
        except Exception as err:
            print(f"Attempt {attempt+1} exception: {err}")
        time.sleep(10)
        
    raise RuntimeError("Failed to create OpenSearch Serverless index after multiple attempts.")

def create_knowledge_base(role_arn, collection_arn):
    print("Creating Bedrock Knowledge Base...")
    
    collection_id = collection_arn.split("/")[-1]
    
    # Check if KB already exists with name
    try:
        kbs = bedrock_agent.list_knowledge_bases(maxResults=50)
        for kb in kbs.get("knowledgeBaseSummaries", []):
            if kb["name"] == KB_NAME:
                kb_id = kb["knowledgeBaseId"]
                print(f"Knowledge Base '{KB_NAME}' already exists. ID: {kb_id}")
                print("Checking status of existing Knowledge Base...")
                while True:
                    kb_res = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)
                    status = kb_res["knowledgeBase"]["status"]
                    print(f"Existing Knowledge Base status: {status}...")
                    if status == "ACTIVE":
                        break
                    elif status in ("FAILED", "DELETE_FAILED"):
                        raise RuntimeError(f"Existing Knowledge Base is in failed state: {status}")
                    time.sleep(5)
                return kb_id
    except Exception as e:
        print(f"Warning checking existing KBs: {e}")

    try:
        res = bedrock_agent.create_knowledge_base(
            name=KB_NAME,
            description="EU AI Act Knowledge Base",
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": {
                    "embeddingModelArn": f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v1"
                }
            },
            storageConfiguration={
                "type": "OPENSEARCH_SERVERLESS",
                "opensearchServerlessConfiguration": {
                    "collectionArn": collection_arn,
                    "vectorIndexName": INDEX_NAME,
                    "fieldMapping": {
                        "vectorField": "bedrock-vector",
                        "textField": "AMAZON_BEDROCK_TEXT_CHUNK",
                        "metadataField": "AMAZON_BEDROCK_METADATA"
                    }
                }
            }
        )
        kb_id = res["knowledgeBase"]["knowledgeBaseId"]
        print(f"Successfully created Knowledge Base. ID: {kb_id}")
        
        # Poll for ACTIVE status
        print("Waiting for Knowledge Base to become ACTIVE...")
        while True:
            kb_res = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)
            status = kb_res["knowledgeBase"]["status"]
            print(f"Knowledge Base status: {status}...")
            if status == "ACTIVE":
                break
            elif status in ("FAILED", "DELETE_FAILED"):
                raise RuntimeError(f"Knowledge Base creation failed with status: {status}")
            time.sleep(5)
            
        return kb_id
    except Exception as e:
        print(f"Failed to create Knowledge Base: {e}")
        raise e

def create_data_source(kb_id):
    print("Creating Bedrock Data Source...")
    
    # Check if data source already exists
    try:
        dss = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)
        for ds in dss.get("dataSourceSummaries", []):
            if ds["name"] == f"{KB_NAME}-S3-Source":
                print(f"Data source already exists. ID: {ds['dataSourceId']}")
                return ds["dataSourceId"]
    except Exception as e:
        print(f"Warning checking data sources: {e}")

    try:
        res = bedrock_agent.create_data_source(
            knowledgeBaseId=kb_id,
            name=f"{KB_NAME}-S3-Source",
            description=f"S3 data source pointing to bucket {S3_BUCKET}",
            dataSourceConfiguration={
                "type": "S3",
                "s3Configuration": {
                    "bucketArn": f"arn:aws:s3:::{S3_BUCKET}"
                }
            }
        )
        ds_id = res["dataSource"]["dataSourceId"]
        print(f"Successfully created Data Source. ID: {ds_id}")
        return ds_id
    except Exception as e:
        print(f"Failed to create Data Source: {e}")
        raise e

def sync_data_source(kb_id, ds_id):
    print("Starting ingestion sync job for Data Source...")
    try:
        res = bedrock_agent.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id
        )
        job_id = res["ingestionJob"]["ingestionJobId"]
        print(f"Ingestion job started. ID: {job_id}")
        
        while True:
            job_res = bedrock_agent.get_ingestion_job(
                knowledgeBaseId=kb_id,
                dataSourceId=ds_id,
                ingestionJobId=job_id
            )
            status = job_res["ingestionJob"]["status"]
            print(f"Sync status: {status}...")
            if status in ("COMPLETE", "COMPLETED"):
                print("✅ Ingestion job completed successfully.")
                break
            elif status in ("FAILED", "STOPPED"):
                print(f"❌ Ingestion job failed with status: {status}")
                if "failureReasons" in job_res["ingestionJob"]:
                    print(f"Reasons: {job_res['ingestionJob']['failureReasons']}")
                break
            time.sleep(10)
    except Exception as e:
        print(f"Warning starting ingestion: {e}")

def update_runtime_execution_role_policy(kb_id):
    print("Updating AgentCore Runtime Execution Role policy to allow bedrock:Retrieve...")
    iam_client = boto3.client("iam", region_name=REGION)
    try:
        roles = iam_client.list_roles(MaxItems=100)
        target_role_name = None
        for role in roles.get("Roles", []):
            if "ApplicationAgentMyAgentRu" in role["RoleName"] and "ragAgent-defaul" in role["RoleName"]:
                target_role_name = role["RoleName"]
                break
        
        if not target_role_name:
            print("Warning: Could not find AgentCore Runtime Execution Role.")
            return
            
        print(f"Found runtime execution role: {target_role_name}")
        
        policy_name = "AgentCore-Runtime-KB-Retrieve-Policy"
        policy_doc = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:Retrieve"
                    ],
                    "Resource": [
                        f"arn:aws:bedrock:us-east-1:{sts.get_caller_identity()['Account']}:knowledge-base/{kb_id}"
                    ]
                }
            ]
        }
        
        iam_client.put_role_policy(
            RoleName=target_role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_doc)
        )
        print("Successfully attached bedrock:Retrieve policy to the runtime execution role.")
    except Exception as e:
        print(f"Error updating runtime role policy: {e}")

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
    print("--- Starting Bedrock KB Creation Automation ---")
    user_arn = get_current_user_arn()
    print(f"Current User/Role ARN: {user_arn}")
    
    role_arn = create_or_get_role()
    create_aoss_policies(user_arn, role_arn)
    collection_id, endpoint = create_aoss_collection()
    
    collection_arn = f"arn:aws:aoss:{REGION}:{sts.get_caller_identity()['Account']}:collection/{collection_id}"
    
    create_vector_index(endpoint)
    
    # Wait a bit for index availability
    time.sleep(10)
    
    kb_id = create_knowledge_base(role_arn, collection_arn)
    update_runtime_execution_role_policy(kb_id)
    ds_id = create_data_source(kb_id)
    sync_data_source(kb_id, ds_id)
    
    print("\n" + "=" * 55)
    print(f"🚀 BEDROCK KNOWLEDGE BASE SUCCESSFULLY CREATED AND SYNCED!")
    print(f"KNOWLEDGE_BASE_ID: {kb_id}")
    print("=" * 55 + "\n")

if __name__ == "__main__":
    main()
