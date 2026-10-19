# Robô X — teste técnico

Este repositório está configurado para **teste manual de rascunho**, sem publicação real e sem execução diária ainda.

## 1. Guardar chave com segurança
No GitHub, abra **Settings → Secrets and variables → Actions → New repository secret**.
- Name: `BUFFER_API_KEY`
- Secret: sua chave de API do Buffer (NUNCA escrever em arquivo ou commit).

## 2. Rodar o teste
Em **Actions → Testar rascunho no Buffer → Run workflow**.

O script consulta organizações e canais, exige encontrar exatamente uma conta X/Twitter e cria um post com `saveToDraft: true`. **Não publica**.

## 3. Conferir
No Buffer, procure o rascunho de teste no canal X. No GitHub Actions, confira se o job informou `SUCESSO`.

## Próxima fase
Depois da confirmação técnica, definir persona, modelo gratuito de geração de texto, horário, limites, revisão e publicação. Não ativar publicação automática sem esta validação.

Documentação: https://developers.buffer.com/examples/create-draft-post.html
