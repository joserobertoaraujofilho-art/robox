#!/usr/bin/env python3
"""Teste seguro: cria somente um rascunho no Buffer, nunca publica."""
import json
import os
import sys
import urllib.request
import urllib.error

API = "https://api.buffer.com"
KEY = os.getenv("BUFFER_API_KEY", "")
if not KEY:
    sys.exit("ERRO: configure BUFFER_API_KEY em Settings > Secrets and variables > Actions.")

def gql(query, variables=None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = urllib.request.Request(API, data=payload, headers={
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"
    }, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=25) as r:
            response = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Erro HTTP do Buffer: {e.code} (verifique a chave/API)")
    if response.get("errors"):
        sys.exit("Erro GraphQL: " + "; ".join(x.get("message", "desconhecido") for x in response["errors"]))
    return response["data"]

account = gql("query { account { organizations { id name } } }")
organizations = account["account"]["organizations"]
candidates = []
for org in organizations:
    data = gql("query ($id: OrganizationId!) { channels(input: { organizationId: $id }) { id name service } }", {"id": org["id"]})
    for channel in data["channels"]:
        if channel["service"].lower() in ("twitter", "x"):
            candidates.append(channel)

if len(candidates) != 1:
    print("Canais X encontrados:", len(candidates))
    for c in candidates:
        print(" -", c["name"])
    sys.exit("É necessário exatamente 1 canal X para evitar postar na conta errada.")

channel = candidates[0]
print("Canal encontrado:", channel["name"])
text = "Teste técnico de integração. Este conteúdo é somente um RASCUNHO e não será publicado."
result = gql("""mutation ($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id text } }
    ... on MutationError { message }
  }
}""", {"input": {
    "text": text, "channelId": channel["id"],
    "schedulingType": "automatic", "mode": "addToQueue", "saveToDraft": True
}})
created = result["createPost"]
if not created.get("post"):
    sys.exit("Buffer recusou criar rascunho: " + created.get("message", "erro desconhecido"))
print("SUCESSO: rascunho criado no Buffer, não publicado.")
print("ID:", created["post"]["id"])
