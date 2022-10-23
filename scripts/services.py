from this import d
from brownie import revocationRegistry, DIDRegistry, IssuerRegistry, credentialRegistry, accounts
import uuid
import requests
import json
import hashlib

contractDeployAccount = accounts[0]
hospital = accounts[1]
doctor = accounts[2]
patient = accounts[3]
verifier = accounts[4]

DID_contract = DIDRegistry.deploy({'from': contractDeployAccount})
issuer_contract = IssuerRegistry.deploy({'from':contractDeployAccount})
revocation_contract = revocationRegistry.deploy({'from':contractDeployAccount})

cred_contract = credentialRegistry.deploy({'from': contractDeployAccount})
mapch_server = "127.0.0.1:5000"
head = {"Content-Type": "application/json"}

def issueCredential(issuing_account, issuer, holder, credential_hash, r, e, n1):
    id = str(uuid.uuid1())
    cred_contract.issueCredential(id, issuer, holder, credential_hash, r, e, n1, {'from': issuing_account})
    
    return id

def getCredential(id, acc):

    _, _, _, cred_hash, cred_r, cred_e, cred_n1 = cred_contract.getCredential(id, {'from': acc})
    
    return {
        "h" : cred_hash,
        "r" : cred_r,
        "N1" : cred_n1,
        "e" : cred_e
    }

def createABEAuthority(authority_name, issuer):
    body = { "authority_name" : authority_name }
    x = requests.post(f"http://{mapch_server}/create_abe_authority", headers=head, json=body)
    maab_master_pk_sk = json.loads(x.text)
    print(maab_master_pk_sk["pk"])
    addMasterKey(maab_master_pk_sk["pk"], issuer)
    return maab_master_pk_sk

def createCHKeys():
    x = requests.get(f"http://{mapch_server}/create_ch_keys", headers=head)
    cham_hash_pk_sk = json.loads(x.text)
    
    return cham_hash_pk_sk

def createABESecretKey(abe_master_key, gid, user_attribute, issuer):
    # if master key is invalid , return None
    if not checkMasterKey(abe_master_key["pk"], issuer):
        return None
    body = {
        "sk" : abe_master_key["sk"],
        "gid" : gid,
        "user_attribute" : [user_attribute]
    }

    x = requests.post(f"http://{mapch_server}/create_abe_attribute_secret_key", headers=head, json=body)
    abe_secret_key = json.loads(x.text)

    addSecretKey(abe_master_key["pk"], abe_secret_key, issuer)
    return abe_secret_key

def generateSupportingCredential(credential, access_policy, ch_pk, ch_sk, authority_abe_pk, issuing_account, official_issuer, holder):
    # if master key is invalid, return none
    if not checkMasterKey(authority_abe_pk, issuing_account):
        return None
    # access policy
    body = {
        "cham_pk" : ch_pk, 
        "cham_sk" : ch_sk,
        "message" : credential,
        "authority_abe_pk" : authority_abe_pk,
        "access_policy" : access_policy
    }

    x = requests.post(f"http://{mapch_server}/hash", headers=head, json=body)
    hash = json.loads(x.text)

    cred_id = issueCredential(issuing_account, official_issuer, holder, hash["h"], hash["r"], hash["e"], hash["N1"])

    return {
        "credential_hash" : hash,
        "credential_id" : cred_id
    }

def verifySupportingCredential(credential_message, credential_id, ch_pk, verifier):

    reconstructed_hash = getCredential(credential_id, verifier)
    
    body = {
        "message" : credential_message,
        "cham_pk" : ch_pk,
        "hash" : reconstructed_hash
    }
    
    x = requests.post(f"http://{mapch_server}/hash_verify", headers=head, json=body)
    hash_res = json.loads(x.text)
    
    return hash_res["is_hash_valid"] == "True"

def adaptSupportingCredential(credential_hash, original_msg, new_msg, cham_pk, gid, abe_secret_key, issuing_account, issuer, holder):
    # if secret key is not valid, return None
    if not checkSecretKey(abe_secret_key, issuing_account):
        return None
    # modify credential
    body = {
        "hash" : credential_hash,
        "original_message" : original_msg,
        "new_message" : new_msg,
        "cham_pk" : cham_pk,
        "gid" : gid,
        "abe_secret_key" : abe_secret_key
    }

    x = requests.post(f"http://{mapch_server}/adapt", headers=head, json=body)
    hash_modified = json.loads(x.text)
    
    # add it to credential registry
    cred_id = issueCredential(issuing_account, issuer, holder, hash_modified["h"], hash_modified["r"], hash_modified["e"], hash_modified["N1"])

    return {
        "credential_hash" : hash_modified,
        "credential_id" : cred_id
    }

def loadCredential(file):
    with open(file, "r") as f:
        return json.dumps(json.load(f))

def registerDID(issuer):
    DID_contract.register(issuer, str("did"+ issuer.address), {"from":  issuer})
    print(DID_contract.getDID(issuer))

def addPubKey(holder, issuer, ch_pk):
    DID_contract.addPublicKey(issuer, holder, str(ch_pk), {"from": issuer})

def registerIssuer(holder, issuer, ch_pk):
    issuer_contract.addIssuer(str(ch_pk), holder, {"from": issuer})

def addMasterKey(abe_master_key, issuer):
    revocation_contract.addMasterKey(str(abe_master_key["egga"]) + str(abe_master_key["gy"]), {"from": issuer})

def addSecretKey(abe_master_key, abe_secret_key, issuer):
    revocation_contract.addSecretKey(str(abe_master_key["egga"]) + str(abe_master_key["gy"]), hashlib.sha256(str(abe_secret_key).encode()).hexdigest(), {"from": issuer})

def revokeMasterKey(abe_master_key, issuer):
    return revocation_contract.revokeMasterKey(str(abe_master_key["egga"]) + str(abe_master_key["gy"]), {"from": issuer})

def revokeSecretKey(abe_secret_key, issuer):
    return revocation_contract.revokeSecretKey(hashlib.sha256(str(abe_secret_key).encode()).hexdigest(), {"from": issuer})

def checkMasterKey(abe_master_key, issuer):
    return revocation_contract.checkMasterKey(str(abe_master_key["egga"]) + str(abe_master_key["gy"]), {"from": issuer})

def checkSecretKey(abe_secret_key, issuer):
    return revocation_contract.checkSecretKey(hashlib.sha256(str(abe_secret_key).encode()).hexdigest(), {"from": issuer})

def main():

    # id = issueCredential(hospital, "did:" + str(hospital.address), "did:" + str(doctor.address), "somehash", "somer", "some e", "somen1")
    # print("id is ===")
    # print(id)

    # ret = getCredential(id, verifier)
    # print("stuff gotten from registry is ===")
    # print(ret)

    ##### SCENARIO 1
    
    # == hospital ==
    print("REGISTER DID ===\n")
    registerDID(hospital)
    
    print("CREATING ABE AUTHORITY ===\n")
    maab_master_pk_sk = createABEAuthority("DOCTORA", hospital)

    print("CHECK FOR MASTER KEY VALIDITY ===\n")
    print(checkMasterKey(maab_master_pk_sk["pk"], hospital))

    print("CREATING CH KEYS ===\n")
    cham_hash_pk_sk = createCHKeys()
    print("CREATING HASH ===\n")
    print("LOADING HASH MESSAGE===\n")
    credential_msg = loadCredential("scripts/supporting_credential_example.json")

    print("ADDING OWNER TO THE BLOCKCHAIN===\n")
    print(cham_hash_pk_sk["pk"])
    addPubKey(hospital, hospital, cham_hash_pk_sk["pk"]["N"])
    registerIssuer(hospital, hospital, cham_hash_pk_sk["pk"]["N"])
    # TODO: fix access policy, should be both patient and doctor
    print("CREATING ACTUAL HASH===\n")
    credential_pack = generateSupportingCredential(credential_msg, "(PATIENT@DOCTORA)", cham_hash_pk_sk["pk"], cham_hash_pk_sk["sk"], maab_master_pk_sk["pk"], hospital, "did:" + str(hospital.address), "did:" + str(doctor.address))
    # action: share credential pack, cham_hash_pk and maab_master_pk_sk with DOCTORA

    ## == doctor ==
    print("REGISTER DID ===\n")
    registerDID(doctor)

    print("VERIFYING HASH ===\n")
    res1 = verifySupportingCredential(credential_msg, credential_pack["credential_id"], cham_hash_pk_sk["pk"], doctor)
    print(res1)
    # TODO: fix, this should be doctor
    print("CREATING ABE SECRET KEY ===\n")
    doctor_abe_secret_key = createABESecretKey(maab_master_pk_sk, "Patient", "PATIENT@DOCTORA", hospital) 
    print(doctor_abe_secret_key)

    print("ADDING OWNER TO THE BLOCKCHAIN===\n")
    addPubKey(doctor, hospital, cham_hash_pk_sk["pk"]["N"])
    registerIssuer(doctor, hospital, cham_hash_pk_sk["pk"]["N"])

    print("ADAPTING HASH ===\n")
    modified_credential_pack = adaptSupportingCredential(credential_pack["credential_hash"], credential_msg, "stuff", cham_hash_pk_sk["pk"], "Patient", doctor_abe_secret_key, doctor, "did:" + str(doctor.address), "did:" + str(patient.address))
    print("VERIFYING HASH ===\n")
    res2 = verifySupportingCredential("stuff", modified_credential_pack["credential_id"], cham_hash_pk_sk["pk"], verifier)

    print("REVOKE MASTER KEY ===\n")
    revokeMasterKey(maab_master_pk_sk["pk"], hospital)

    print("CHECK FOR MASTER KEY VALIDITY ===\n")
    print(checkMasterKey(maab_master_pk_sk["pk"], hospital))

    print("CHECK FOR ABE SECRET KEY VALIDITY ===\n")
    print(checkSecretKey(doctor_abe_secret_key, hospital))

    print(res2)