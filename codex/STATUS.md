# Codex — preparação parcial, NÃO ativado

## Alterações realizadas em 19/10/2026
- `codex/runner.py`: preparação de geração em modo dry-run, validação de texto, sessão ChatGPT gerenciada e registro de provas em `codex-trials/`. Não contém chamada à API do Buffer nem publica no X.
- `codex/test_runner.py`: 15 testes unitários executados com sucesso no ambiente de desenvolvimento. Esses testes não autenticam nem chamam o modelo.
- `.gitignore`: proteção contra inclusão acidental de arquivos locais de sessão e credenciais.

## Impedimento observado
A tentativa de criar `.github/workflows/codex-geracao-teste.yml` pela conexão ChatGPT/GitHub retornou:

> Esta ferramenta foi bloqueada pelas configurações de segurança da OpenAI. Verifique novamente o que está enviando.

O arquivo não foi instalado. Não foi tentado outro método para contornar o bloqueio. O workflow de login inicial que havia sido criado foi removido para não incentivar uma autenticação para uma instalação incompleta.

## Estado e limites
- Novo gerador Codex: não ativado, não autenticado e não testado com o modelo real.
- Nenhuma publicação no X ou Buffer foi realizada nesta preparação.
- Nenhuma credencial de Codex foi solicitada, lida ou adicionada.
- O repositório continuava público na última verificação. A conexão disponível não ofereceu ação de alteração de visibilidade.
- Os workflows anteriores do projeto não foram modificados. As tarefas anteriores do ChatGPT não foram alteradas nesta preparação.
- Testes unitários aprovados não equivalem a geração agendada nem a publicação autônoma comprovada.

## Referências técnicas consultadas
- https://developers.openai.com/codex/auth/ci-cd-auth
- https://developers.openai.com/codex/auth
- https://developers.openai.com/codex/non-interactive-mode

Não guardar `auth.json`, senhas ou tokens em commits, artefatos públicos ou conversas. A documentação oficial exige ambiente privado e confiável para autenticação gerenciada de conta em CI/CD.
