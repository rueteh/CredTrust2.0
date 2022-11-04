from this import d
from brownie import credentialRegistry, accounts, VoteRegistry, IssuerRegistry
import uuid
import requests
import json
import rsa
from cryptography.fernet import Fernet

from scripts.MAPCH import chamwithemp
from scripts.MAPCH import MAABE
from charm.toolbox.pairinggroup import PairingGroup, GT
from json import dumps, loads
from charm.toolbox.symcrypto import AuthenticatedCryptoAbstraction,SymmetricCryptoAbstraction
from charm.core.math.pairing import hashPair as extractor
from charm.toolbox.integergroup import integer
import re

contractDeployAccount = accounts[0]
hospital = accounts[1]
doctor = accounts[2]
patient = accounts[3]
verifier = accounts[4]
relative = accounts[5]
voter = accounts[6]
hospital_rc = accounts[7]

cred_contract = credentialRegistry.deploy({'from': contractDeployAccount})
vote_contract = VoteRegistry.deploy({'from': contractDeployAccount})
issuer_contract = IssuerRegistry.deploy({'from': contractDeployAccount})
mapch_server = "127.0.0.1:5000"
head = {"Content-Type": "application/json"}

groupObj = PairingGroup('SS512')
maabe = MAABE.MaabeRW15(groupObj)
public_parameters = maabe.setup()

# helpers

def cut_text(text,lenth): 
    textArr = re.findall('.{'+str(lenth)+'}', text) 
    textArr.append(text[(len(textArr)*lenth):]) 
    return textArr

def merge_dicts(*dict_args):
    """
    Given any number of dicts, shallow copy and merge into a new dict,
    precedence goes to key value pairs in latter dicts.
    """
    result = {}
    for dictionary in dict_args:
        result.update(dictionary)
    return result

# credential

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

def createABEAuthority(authority_name):
    (pk, sk) = maabe.authsetup(public_parameters, authority_name)
    return {"pk" : pk, "sk" : sk}

def createCHKeys(hash_func):
    (pk, sk) = hash_func.keygen(1024)
    return {"pk" : pk, "sk" : sk}

def createABESecretKey(abe_master_sk, gid, user_attribute):
    return maabe.multiple_attributes_keygen(public_parameters, abe_master_sk, gid, user_attribute)

def createHash(cham_pk, cham_sk, msg, hash_func, abe_master_pk, access_policy):
    xi = hash_func.hash(cham_pk, cham_sk, msg)
    etd = [xi['p1'],xi['q1']]
    
    maabepk = { abe_master_pk["name"] : abe_master_pk }

    # encrypt
    rand_key = groupObj.random(GT)
    #if debug: print("msg =>", rand_key)
    #encrypt rand_key
    maabect = maabe.encrypt(public_parameters, maabepk, rand_key, access_policy)
    #rand_key->symkey AE  
    symcrypt = AuthenticatedCryptoAbstraction(extractor(rand_key))
    #symcrypt msg(etd=(p1,q1))
    etdtostr = [str(i) for i in etd]
    etdsumstr = etdtostr[0]+etdtostr[1]
    symct = symcrypt.encrypt(etdsumstr)

    ct = {'rkc':maabect,'ec':symct}

    #if debug: print("\n\nCiphertext...\n")
    #groupObj.debug(ct)
    #print("ciphertext:=>", ct)
    h = {'h': xi['h'], 'r': xi['r'], 'cipher':ct, 'N1': xi['N1'], 'e': xi['e']}
    return h

def generateAndIssueSupportingCredential(supporting_credential, hash_funcs, access_policy, ch_pk, ch_sk, authority_abe_pk, issuing_account, official_issuer, holder):

    supporting_credential_msg = supporting_credential
    supporting_credential_msg[0]["Officialissuer"] = official_issuer

    # credential hashing
    block1_original_hash = createHash(ch_pk, ch_sk, json.dumps(supporting_credential_msg[0]), hash_funcs[0], authority_abe_pk, access_policy)
    block2_original_hash = createHash(ch_pk, ch_sk, json.dumps(supporting_credential_msg[1]), hash_funcs[1], authority_abe_pk, access_policy)
    block3_original_hash = createHash(ch_pk, ch_sk, json.dumps(supporting_credential_msg[2]), hash_funcs[2], authority_abe_pk, access_policy)
    
    block1_cred_id = issueCredential(issuing_account, official_issuer, holder, block1_original_hash["h"], block1_original_hash["r"], block1_original_hash["e"], block1_original_hash["N1"])
    block2_cred_id = issueCredential(issuing_account, official_issuer, holder, block2_original_hash["h"], block2_original_hash["r"], block2_original_hash["e"], block2_original_hash["N1"])
    block3_cred_id = issueCredential(issuing_account, official_issuer, holder, block3_original_hash["h"], block3_original_hash["r"], block3_original_hash["e"], block3_original_hash["N1"])    
    
    credential_uuid_list = block1_cred_id + "_" + block2_cred_id + "_" + block3_cred_id

    metadata_hash = createHash(ch_pk, ch_sk, credential_uuid_list, hash_funcs[3], authority_abe_pk, access_policy)
    metadata_id = issueCredential(issuing_account, official_issuer, holder, metadata_hash["h"], metadata_hash["r"], metadata_hash["e"], metadata_hash["N1"])

    # voting 
    voting_required = False

    if (supporting_credential_msg[0]["scenario"] in ["InPatient"]):
        voting_required = True

    vote_contract.addCredential(metadata_id, voting_required, supporting_credential_msg[0]["numVotesRequired"], {'from': issuing_account})

    # adding to issuer registry
    issuer_contract.addIssuer(official_issuer, "PCH", str(ch_pk["N"]), {'from': issuing_account})

    # collate supporting credential

    supporting_credential = {
        "metadata": {
            "msg" : credential_uuid_list,
            "hash" : metadata_hash,
            "id" : metadata_id
        },
        "block1" : {
            "msg" : json.dumps(supporting_credential_msg[0]),
            "hash" : block1_original_hash,
            "id" : block1_cred_id
        },
        "block2" : {
            "msg" : json.dumps(supporting_credential_msg[1]),
            "hash" : block2_original_hash,
            "id" : block2_cred_id
        },
        "block3" : {
            "msg" : json.dumps(supporting_credential_msg[2]),
            "hash" : block3_original_hash,
            "id" : block3_cred_id
        }
    }

    return supporting_credential

def verifySupportingCredential(supporting_credential, ch_pk, hash_funcs):

    msg = supporting_credential["block1"]["msg"]
    official_issuer = (json.loads(msg))["Officialissuer"]

    check_public_key = issuer_contract.checkIssuer(official_issuer, "PCH", str(ch_pk["N"]), {'from': accounts[0]})

    chamHash1 = hash_funcs[0]
    chamHash2 = hash_funcs[1]
    chamHash3 = hash_funcs[2]

    cred_registry_check1 = cred_contract.checkCredential(supporting_credential["metadata"]["id"], supporting_credential["metadata"]["hash"]["h"], supporting_credential["metadata"]["hash"]["r"], supporting_credential["metadata"]["hash"]["e"], supporting_credential["metadata"]["hash"]["N1"], {'from': accounts[0]})
    cred_registry_check2 = cred_contract.checkCredential(supporting_credential["block1"]["id"], supporting_credential["block1"]["hash"]["h"], supporting_credential["block1"]["hash"]["r"], supporting_credential["block1"]["hash"]["e"], supporting_credential["block1"]["hash"]["N1"],{'from': accounts[0]})
    cred_registry_check3 = cred_contract.checkCredential(supporting_credential["block2"]["id"], supporting_credential["block2"]["hash"]["h"], supporting_credential["block2"]["hash"]["r"], supporting_credential["block2"]["hash"]["e"], supporting_credential["block2"]["hash"]["N1"],{'from': accounts[0]})
    cred_registry_check4 = cred_contract.checkCredential(supporting_credential["block3"]["id"], supporting_credential["block3"]["hash"]["h"], supporting_credential["block3"]["hash"]["r"], supporting_credential["block3"]["hash"]["e"], supporting_credential["block3"]["hash"]["N1"],{'from': accounts[0]})

    if (check_public_key and cred_registry_check1 and cred_registry_check2 and cred_registry_check3 and cred_registry_check4):
        block1_verify_res = chamHash1.hashcheck(ch_pk, supporting_credential["block1"]["msg"], supporting_credential["block1"]["hash"])
        block2_verify_res = chamHash2.hashcheck(ch_pk, supporting_credential["block2"]["msg"], supporting_credential["block2"]["hash"])
        block3_verify_res = chamHash3.hashcheck(ch_pk, supporting_credential["block3"]["msg"], supporting_credential["block3"]["hash"])

        return (block1_verify_res and block2_verify_res and block3_verify_res)

    else:
        return False

def collision(original_msg, new_msg, h, hash_func, ch_pk, abe_secret_key, gid):
    
    user_sk = {'GID': gid, 'keys': merge_dicts(abe_secret_key)}
    
    #decrypt rand_key
    rec_key = maabe.decrypt(public_parameters, user_sk, h['cipher']['rkc'])
    #rec_key->symkey AE
    rec_symcrypt = AuthenticatedCryptoAbstraction(extractor(rec_key))
    #symdecrypt rec_etdsumstr
    rec_etdsumbytes = rec_symcrypt.decrypt(h['cipher']['ec'])
    rec_etdsumstr = str(rec_etdsumbytes, encoding="utf8")
    #print("etdsumstr type=>",type(rec_etdsumstr))
    #sumstr->etd str list
    rec_etdtolist = cut_text(rec_etdsumstr, 309)
   # print("rec_etdtolist=>",rec_etdtolist)
    #etd str list->etd integer list
    rec_etdint = {'p1': integer(int(rec_etdtolist[0])),'q1':integer(int(rec_etdtolist[1]))}
    #print("rec_etdint=>",rec_etdint)
    r1 = hash_func.collision(original_msg, new_msg, h, rec_etdint, ch_pk)
    #if debug: print("new randomness =>", r1)
    new_h = {'h': h['h'], 'r': r1, 'cipher': h['cipher'], 'N1': h['N1'], 'e': h['e']}
    return new_h

def adaptSupportingCredentialBlock(supporting_credential, block, hash_func, ch_pk, abe_secret_key, gid):

    block_original = supporting_credential[block]["msg"]
    block_modified = block_original
    block_modified = json.loads(block_modified)
    block_modified["credentialSubject"]["permissions"] = ["some permissions 2"]
    block_modified = json.dumps(block_modified)

    hash_modified = collision(block_original, block_modified, supporting_credential[block]["hash"], hash_func, ch_pk, abe_secret_key, gid)
    
    modified_supporting_credential = supporting_credential

    modified_supporting_credential[block]["hash"] = hash_modified
    modified_supporting_credential[block]["msg"] = block_modified

    return modified_supporting_credential

def loadCredential(file):
    with open(file, "r") as f:
        return json.load(f)

def issueAdaptedSupportingCredential(supporting_credential, block, issuer_account, issuer, holder, ch_pk):
    if (checkVoting(supporting_credential["metadata"]["id"], issuer_account)):
        cred_contract.issueCredential(supporting_credential[block]["id"], issuer, holder, supporting_credential[block]["hash"]["h"], supporting_credential[block]["hash"]["r"], supporting_credential[block]["hash"]["e"], supporting_credential[block]["hash"]["N1"], {'from': issuer_account})
        
        # adding to issuer registry
        issuer_contract.addIssuer(issuer, "PCH", str(ch_pk["N"]), {'from': issuer_account})

        return True
    else:
        return False

## voting

def checkVoting(credential_id, issuer_account):
    return vote_contract.isVotingCompleted(credential_id, {'from': issuer_account})

def addVoteAndTryUpdateCredential(supporting_credential, role_credential_pack, role_credential_pk, block, voting_account, issuer_account, issuer, holder, ch_pk, rc_issuer):
    
    # role credential
    concat_rc_keys = str(role_credential_pk) + "_" + str(role_credential_pack["encryped_key"])

    if (issuer_contract.checkIssuer(rc_issuer, "RSA_FERMAT", concat_rc_keys, {'from': accounts[0]}) == False):
        print("ROLE CREDENTIAL KEYS ARE NOT VALID")
        return False
    
    decryped_sym_key = rsa.decrypt(role_credential_pack["encryped_key"], role_credential_pk)
    fernet = Fernet(decryped_sym_key)    

    decryped_rc = fernet.decrypt(role_credential_pack["role_credential"]).decode()
    json_rc = json.loads(decryped_rc)

    block1 = json.loads(supporting_credential["block1"]["msg"])
    voter_did = json_rc["credentialSubject"]["id"]

    if (json_rc["credentialSubject"]["role"] in block1["approvalPolicty"]):
        vote_contract.vote(supporting_credential["metadata"]["id"], voter_did, {'from': voting_account})
        return issueAdaptedSupportingCredential(supporting_credential, block, issuer_account, issuer, holder, ch_pk)

    else:
        print("COULD NOT VOTE BECAUSE VOTER DOES NOT HAVE THE RIGHT ROLE")
        return False

def issueRoleCredential(rc_rsapkey, rc_rsaskey, rc_symkey, issuer, holder_did):
    rc_json = loadCredential("role_credential_example.json")
    rc_json["credentialSubject"]["id"] = holder_did
    rc_msg = json.dumps(rc_json)

    fernet = Fernet(rc_symkey)
    enc_rc = fernet.encrypt(rc_msg.encode())
    enc_key = rsa.encrypt(rc_symkey, rc_rsaskey)

    cred_id = issueCredential(issuer, "did:" + str(issuer.address), holder_did, enc_rc, "", "", "")

    concat_key = str(rc_rsapkey) + "_" + str(enc_key)

    issuer_contract.addIssuer("did:" + str(issuer.address), "RSA_FERMAT", concat_key, {'from': issuer})

    return {
        "id" : cred_id, 
        "encryped_key" : enc_key,
        "role_credential" : enc_rc
    }

def main():

    # == hospital ==
    print("CREATING ABE AUTHORITY ===\n")
    maab_master_pk_sk = createABEAuthority("DOCTORA")

    print("CREATING CH KEYS ===\n")
    chamHash1 = chamwithemp.Chamwithemp()
    cham_hash_pk_sk = createCHKeys(chamHash1)

    chamHash2 = chamwithemp.Chamwithemp()
    _ = createCHKeys(chamHash2)

    chamHash3 = chamwithemp.Chamwithemp()
    _ = createCHKeys(chamHash3)

    chamHash4 = chamwithemp.Chamwithemp()
    _ = createCHKeys(chamHash4)

    print("CREATING REGULAR ROLE CREDENTIAL KEYS ===\n")
    rc_sk, rc_pk = rsa.newkeys(512)
    rc_symkey = Fernet.generate_key()

    print("CREATING AND ISSUING SUPPORTING CREDENTIAL ===\n")
    credential_msg_json = loadCredential("supporting_credential_example.json")
    supporting_credential = generateAndIssueSupportingCredential(credential_msg_json, [chamHash1, chamHash2 , chamHash3, chamHash4], "(DOCTOR@DOCTORA or PATIENT@DOCTORA)",
                                                                 cham_hash_pk_sk["pk"], cham_hash_pk_sk["sk"], maab_master_pk_sk["pk"], 
                                                                 hospital, "did:" + str(hospital.address), "did:" + str(doctor.address))

    # action: share credential pack, cham_hash_pk and maab_master_pk_sk with DOCTORA

    # == doctor ==
    print("VERIFYING SUPPORTING CREDENTIAL ===\n")
    print(verifySupportingCredential(supporting_credential, cham_hash_pk_sk["pk"], [chamHash1, chamHash2 , chamHash3]))

    print("CREATING ABE SECRET KEY FOR DOCTOR ===\n")
    doctor_abe_secret_key = createABESecretKey(maab_master_pk_sk["sk"], "Doctor", ["DOCTOR@DOCTORA"]) 

    print("ADAPTING BLOCK 2 HASH (Doctor) ===\n")
    supporting_credential = adaptSupportingCredentialBlock(supporting_credential, "block2", chamHash2, cham_hash_pk_sk["pk"], doctor_abe_secret_key, "Doctor")

    print("TRY SHARE MODIFIED CREDENTIAL WITHOUT VOTES ===\n")
    try_issue_doctor_modified_sc = issueAdaptedSupportingCredential(supporting_credential, "block2", doctor , "did:" + str(doctor.address), "did:" + str(patient.address), cham_hash_pk_sk["pk"])
    print(try_issue_doctor_modified_sc)

    print("BEGIN VOTING PROCESS ===\n")
    
    print("ISSUING ROLE CREDENTIAL ===\n")
    role_credential_pack = issueRoleCredential(rc_pk, rc_sk, rc_symkey, hospital_rc, "did" + str(voter.address))

    print("ADDING VOTE ===\n")
    addVoteAndTryUpdateCredential(supporting_credential, role_credential_pack, rc_pk, "block2", voter, doctor, "did:" + str(doctor.address), "did:" + str(patient.address), cham_hash_pk_sk["pk"], "did:" + str(hospital_rc.address))

    print("VERIFYING DOCTOR MODIFIED SUPPORTING CREDENTIAL (Doctor) ===\n")
    print(verifySupportingCredential(supporting_credential, cham_hash_pk_sk["pk"], [chamHash1, chamHash2 , chamHash3]))

    print("CREATING ABE SECRET KEY FOR PATIENT 1 ===\n")
    patient1_abe_secret_key = createABESecretKey(maab_master_pk_sk["sk"], "Patient1", ["PATIENT@DOCTORA"])

    # action flow: share key and credential with patient

    # == Patient ==
    print("ADAPTING BLOCK 3 (Patient 1) ===\n")
    supporting_credential = adaptSupportingCredentialBlock(supporting_credential, "block3", chamHash3, cham_hash_pk_sk["pk"], patient1_abe_secret_key, "Patient1")

    try_issue_patient_modified_sc = issueAdaptedSupportingCredential(supporting_credential, "block3", patient , "did:" + str(patient.address), "did:" + str(relative.address), cham_hash_pk_sk["pk"])
    print(try_issue_patient_modified_sc)

    # == Relative ==

    print("VERIFYING HASH (Relative/Verifier) ===\n")
    print(verifySupportingCredential(supporting_credential, cham_hash_pk_sk["pk"], [chamHash1, chamHash2 , chamHash3]))