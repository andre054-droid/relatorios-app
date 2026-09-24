"""
Aplicação para análise de Relatórios Resumo CERTIS (MP6)
- Aceita vários relatórios de uma vez
- Extrai Nome, NIF, Não Conformidades
- Classifica APTO / NÃO APTO
- Permite upload de documentos por cada requisito
"""

import streamlit as st
import pdfplumber
import re
import json
from datetime import datetime
from pathlib import Path
import pandas as pd
from io import BytesIO

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
UPLOAD_DIR = Path(__file__).parent / "uploads"
DATA_DIR = Path(__file__).parent / "data"
UPLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

st.set_page_config(
    page_title="CERTIS - Análise de Relatórios",
    page_icon="📋",
    layout="wide"
)

# Tipos de documentos conhecidos
DOC_TYPES = {
    "Análise de Solos (S25)": {
        "keywords": ["análise", "solo", "s25", "matéria orgânica", "fósforo extraível", "egner", "utad", "labsolos"],
        "filename": ["s25", "solo", "solos"],
        "fecha": ["Análises", "Análise de solos"]
    },
    "Análise de Folhas (P25)": {
        "keywords": ["análise", "folha", "folhas", "pecíolo", "p25", "azoto", "dris", "órgão"],
        "filename": ["p25", "folha", "folhas", "pecíolo"],
        "fecha": ["Análises de folhas", "Análises"]
    },
    "DCP / Declaração de Colheita": {
        "keywords": ["declaração de colheita", "dcp", "instituto da vinha", "colheita e produção", "uva apta"],
        "filename": ["dcp", "colheita", "declaração"],
        "fecha": ["DCP ou talões de venda", "DCP"]
    },
    "Caderno de Campo": {
        "keywords": ["caderno de campo", "registo de proteção fitossanitária", "operações culturais", "beneficiário"],
        "filename": ["caderno", "campo", "v6", "registo"],
        "fecha": ["Caderno de campo atualizado", "Caderno de campo"]
    },
    "Cartão de Aplicador": {
        "keywords": ["aplicador de produtos fitofarmacêuticos", "cartão", "válido até", "drap"],
        "filename": ["cartão", "aplicador", "cartao"],
        "fecha": ["Cartão de aplicador válido", "Cartão de aplicador"]
    },
    "Guia de Valorfito": {
        "keywords": ["valorfito", "sigeru", "embalagens", "resíduos", "entrega de resíduos"],
        "filename": ["valorfito", "valorifito", "embalagens", "sigeru"],
        "fecha": ["Guia de valorfito"]
    },
    "Inspeção de Pulverizador": {
        "keywords": ["inspeção periódica", "pulverizador", "selo de inspeção", "ipp", "certificado de inspeção", "equipamentos de aplicação"],
        "filename": ["inspeção", "inspecao", "pulverizador", "ipp"],
        "fecha": ["Comprovativo do pulverizador válido", "Inspeção de pulverizador"]
    },
    "Faturas / Documentos contabilísticos": {
        "keywords": ["fatura", "factura", "talão", "venda", "compra"],
        "filename": ["fatura", "talão", "talao"],
        "fecha": ["Faturas de compras/vendas", "DCP ou talões de venda"]
    },
}


def classify_document(filename: str, text_content: str = "") -> str:
    fname = filename.lower()
    content = (text_content or "").lower()
    scores = {}
    for doc_type, rules in DOC_TYPES.items():
        score = 0
        for kw in rules["filename"]:
            if kw in fname:
                score += 3
        for kw in rules["keywords"]:
            if kw in content:
                score += 1
        scores[doc_type] = score
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "Outro / Não identificado"


def extract_text_from_file(uploaded_file) -> str:
    name = uploaded_file.name.lower()
    if name.endswith(".pdf"):
        try:
            with pdfplumber.open(uploaded_file) as doc:
                return "\n".join((page.extract_text() or "") for page in doc.pages)
        except Exception:
            return ""
    return ""


def extract_text_from_pdf(pdf_file) -> str:
    with pdfplumber.open(pdf_file) as doc:
        return "\n".join((page.extract_text() or "") for page in doc.pages)


def parse_report(text: str) -> dict:
    result = {
        "nome": None,
        "nif": None,
        "codigo_interno": None,
        "data": None,
        "area": None,
        "produto": None,
        "modo_producao": None,
        "nao_conformidades": [],
        "informacoes": [],
        "estado": "APTO"
    }

    m = re.search(r"Identificação de Cliente:\s*(.+?)(?:\n|NIF)", text, re.IGNORECASE)
    if m:
        result["nome"] = m.group(1).strip()

    m = re.search(r"NIF do Operador:\s*(\d+)", text)
    if m:
        result["nif"] = m.group(1)

    m = re.search(r"Código Interno:\s*(\S+)", text)
    if m:
        result["codigo_interno"] = m.group(1)

    m = re.search(r"Data:\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        result["data"] = m.group(1)

    m = re.search(r"Área Total\s*([\d.,]+)\s*Ha", text)
    if m:
        result["area"] = m.group(1) + " Ha"

    m = re.search(
        r"(Vinha Sequeiro.*?(?:PRODI|AB)|Olival Sequeiro.*?(?:PRODI|AB)|[A-Za-zÀ-ú\s]+\([A-Za-zÀ-ú\s]+\))\s+(PRODI|AB)\s+([\d.,]+)\s*Ha",
        text
    )
    if m:
        result["produto"] = m.group(1).strip()
        result["modo_producao"] = m.group(2)

    nc_match = re.search(
        r"Não Conformidades\s*(.*?)(?:Informações|Informações a constar|$)",
        text, re.DOTALL | re.IGNORECASE
    )
    if nc_match:
        nc_block = re.sub(r"Edição nº.*", "", nc_match.group(1), flags=re.DOTALL)
        lines = [l.strip() for l in nc_block.splitlines() if l.strip()]
        current_nc = None

        for line in lines:
            if re.match(r"^(Código|Descrição|Evidências)", line, re.IGNORECASE):
                continue
            code_match = re.match(r"^(\d+(?:\.\d+)?)\s+(.+)$", line)
            if code_match:
                if current_nc:
                    result["nao_conformidades"].append(current_nc)
                current_nc = {
                    "codigo": code_match.group(1),
                    "descricao": code_match.group(2).strip(),
                    "evidencias": "",
                    "documentos_pedidos": []
                }
            elif current_nc:
                current_nc["descricao"] += " " + line
                lower = line.lower()
                if any(k in lower for k in ["deve enviar", "deve o operador", "comprovativo", "caderno", "cartão", "guia", "dcp", "talão", "análise", "pulverizador"]):
                    current_nc["evidencias"] += " " + line

        if current_nc:
            result["nao_conformidades"].append(current_nc)

    for nc in result["nao_conformidades"]:
        nc["descricao"] = re.sub(r"\s+", " ", nc["descricao"]).strip()
        evid = (nc.get("evidencias", "") + " " + nc["descricao"]).lower()
        docs = []
        if "caderno de campo" in evid:
            docs.append("Caderno de campo atualizado")
        if "pulverizador" in evid:
            docs.append("Comprovativo do pulverizador válido")
        if "dcp" in evid or "talões de venda" in evid or "talão" in evid:
            docs.append("DCP ou talões de venda")
        if "guia de valorfito" in evid or "valorifito" in evid or "valorfito" in evid:
            docs.append("Guia de valorfito")
        if "cartão de aplicador" in evid:
            docs.append("Cartão de aplicador válido")
        if "análises de folhas" in evid or "análise de folhas" in evid:
            docs.append("Análises de folhas")
        if "análises" in evid and "folhas" not in evid:
            docs.append("Análises")
        if "faturas" in evid:
            docs.append("Faturas de compras/vendas")
        nc["documentos_pedidos"] = list(dict.fromkeys(docs))

    info_match = re.search(
        r"Informações\s*(.*?)(?:Informações a constar no Certificado|NOTA 1|$)",
        text, re.DOTALL | re.IGNORECASE
    )
    if info_match:
        info_block = re.sub(r"Edição nº.*", "", info_match.group(1), flags=re.DOTALL)
        for m in re.finditer(r"^(\d+(?:\.\d+)?)\s+(.+?)(?=\n\d+(?:\.\d+)?\s+|\n*$)", info_block, re.MULTILINE | re.DOTALL):
            result["informacoes"].append({
                "codigo": m.group(1),
                "texto": re.sub(r"\s+", " ", m.group(2)).strip()
            })

    result["estado"] = "NÃO APTO" if result["nao_conformidades"] else "APTO"
    return result


def save_state(nif: str, data: dict):
    path = DATA_DIR / f"{nif}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_state(nif: str) -> dict:
    path = DATA_DIR / f"{nif}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Autenticação simples por palavra-passe
# ---------------------------------------------------------------------------
# ALTERA ESTA  para a que quiseres partilhar com os colegas
PASSWORD = "certis"

def check_password():
    """Retorna True se a password estiver correta."""
    if st.session_state.get("authenticated"):
        return True

    st.title("🔒 Acesso restrito")
    st.write("Introduza a palavra-passe para aceder à aplicação.")
    pwd = st.text_input("Palavra-passe", type="password", key="pwd_input")
    if st.button("Entrar"):
        if pwd == PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Palavra-passe incorreta.")
    return False


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------
if not check_password():
    st.stop()

st.title("📋 Análise de Relatórios CERTIS")
st.caption("Carregue um ou vários Relatórios Resumo (MP6) → veja o estado e os documentos em falta de cada operador")

with st.sidebar:
    st.header("1. Carregar Relatórios")
    uploaded_reports = st.file_uploader(
        "PDFs dos Relatórios Resumo CERTIS",
        type=["pdf"],
        accept_multiple_files=True,
        key="report_uploader"
    )
    if uploaded_reports:
        st.success(f"{len(uploaded_reports)} relatório(s) carregado(s)")

    st.markdown("---")
    if st.button("Sair (terminar sessão)"):
        st.session_state["authenticated"] = False
        st.rerun()

if uploaded_reports:
    try:
        # Processar todos os relatórios
        resultados = []
        for report in uploaded_reports:
            text = extract_text_from_pdf(report)
            parsed = parse_report(text)
            parsed["_filename"] = report.name
            resultados.append(parsed)

        # ----- RESUMO GERAL -----
        st.subheader("Resumo geral")

        resumo_geral = []
        for r in resultados:
            ncs = r["nao_conformidades"]
            docs_em_falta = []
            for nc in ncs:
                if nc["documentos_pedidos"]:
                    docs_em_falta.extend(nc["documentos_pedidos"])
                else:
                    docs_em_falta.append(f"NC {nc['codigo']}: ver descrição")
            # unique
            docs_em_falta = list(dict.fromkeys(docs_em_falta))

            resumo_geral.append({
                "NIF": r["nif"] or "—",
                "Nome": r["nome"] or "—",
                "Código": r["codigo_interno"] or "—",
                "Data": r["data"] or "—",
                "Estado": r["estado"],
                "Nº NCs": len(ncs),
                "Documentos em falta": "; ".join(docs_em_falta) if docs_em_falta else "Nenhum"
            })

        df_geral = pd.DataFrame(resumo_geral)
        st.dataframe(df_geral, use_container_width=True)

        # Contadores
        aptos = sum(1 for r in resultados if r["estado"] == "APTO")
        nao_aptos = len(resultados) - aptos
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Total de relatórios", len(resultados))
        col_b.metric("APTOS", aptos)
        col_c.metric("NÃO APTOS", nao_aptos)

        # Exportar resumo geral
        buffer_geral = BytesIO()
        with pd.ExcelWriter(buffer_geral, engine="openpyxl") as writer:
            df_geral.to_excel(writer, index=False, sheet_name="Resumo Geral")
        st.download_button(
            label="📥 Descarregar resumo geral (Excel)",
            data=buffer_geral.getvalue(),
            file_name=f"resumo_geral_certis_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        st.divider()

        # ----- DETALHE POR OPERADOR -----
        st.subheader("Detalhe por operador")

        # Seletor de operador
        opcoes = {
            f"{r['nome'] or 'Sem nome'} ({r['nif'] or 'sem NIF'}) — {r['estado']}": i
            for i, r in enumerate(resultados)
        }
        escolha = st.selectbox("Escolha o operador para ver em detalhe:", list(opcoes.keys()))
        idx = opcoes[escolha]
        parsed = resultados[idx]

        state = load_state(parsed["nif"] or "unknown")
        if "docs_por_req" not in state:
            state["docs_por_req"] = {}
        if "resolvidas" not in state:
            state["resolvidas"] = {}
        if "classificacoes" not in state:
            state["classificacoes"] = {}

        # Cabeçalho do operador
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.markdown(f"### {parsed['nome'] or '—'}")
            st.write(f"**NIF:** {parsed['nif'] or '—'}")
            st.write(f"**Código interno:** {parsed['codigo_interno'] or '—'}")
        with col2:
            st.write(f"**Data:** {parsed['data'] or '—'}")
            st.write(f"**Área:** {parsed['area'] or '—'}")
            if parsed["produto"]:
                st.write(f"**Produto:** {parsed['produto']}")
        with col3:
            if parsed["estado"] == "APTO":
                st.success("### ✅ APTO")
            else:
                st.error("### ❌ NÃO APTO")

        st.markdown("---")

        if parsed["nao_conformidades"]:
            st.markdown("#### Não Conformidades")

            for nc in parsed["nao_conformidades"]:
                codigo = nc["codigo"]
                resolvida = state["resolvidas"].get(codigo, False)

                if codigo not in state["docs_por_req"]:
                    state["docs_por_req"][codigo] = {}

                with st.expander(
                    f"{'✅' if resolvida else '🔴'}  Código {codigo} — {nc['descricao'][:90]}{'…' if len(nc['descricao']) > 90 else ''}",
                    expanded=not resolvida
                ):
                    st.markdown(f"**Descrição completa:**  \n{nc['descricao']}")
                    st.markdown("---")

                    requisitos = nc["documentos_pedidos"] if nc["documentos_pedidos"] else ["Documento / evidência"]
                    todos_preenchidos = True

                    for idx_req, req in enumerate(requisitos):
                        st.markdown(f"**📌 {req}**")

                        key_safe = re.sub(r"[^a-zA-Z0-9]", "_", req)[:40]
                        uploader_key = f"up_{parsed['nif']}_{codigo}_{key_safe}_{idx_req}"

                        uploaded = st.file_uploader(
                            f"Carregar ficheiro para: {req}",
                            type=["pdf", "png", "jpg", "jpeg", "docx", "xlsx", "xls"],
                            accept_multiple_files=True,
                            key=uploader_key
                        )

                        if uploaded:
                            for f in uploaded:
                                dest_dir = UPLOAD_DIR / (parsed["nif"] or "unknown") / codigo / key_safe
                                dest_dir.mkdir(parents=True, exist_ok=True)
                                dest = dest_dir / f.name
                                with open(dest, "wb") as out:
                                    out.write(f.getbuffer())

                                content = extract_text_from_file(f)
                                doc_type = classify_document(f.name, content)
                                state["classificacoes"][str(dest)] = doc_type

                                if req not in state["docs_por_req"][codigo]:
                                    state["docs_por_req"][codigo][req] = []
                                if str(dest) not in state["docs_por_req"][codigo][req]:
                                    state["docs_por_req"][codigo][req].append(str(dest))

                            st.success(f"Ficheiro(s) guardado(s) para «{req}»")

                        ficheiros_req = state["docs_por_req"][codigo].get(req, [])
                        if ficheiros_req:
                            for p in ficheiros_req:
                                tipo = state["classificacoes"].get(p, "—")
                                st.caption(f"✅ 📎 {Path(p).name}  →  {tipo}")
                        else:
                            st.caption("⬜ Ainda sem documento")
                            todos_preenchidos = False

                        st.markdown("")

                    if todos_preenchidos and requisitos:
                        st.info("Todos os documentos pedidos já têm ficheiro. Pode marcar como resolvida.")

                    resolvida_now = st.checkbox(
                        "Marcar esta Não Conformidade como RESOLVIDA",
                        value=resolvida,
                        key=f"res_{parsed['nif']}_{codigo}"
                    )
                    state["resolvidas"][codigo] = resolvida_now

            if st.button("💾 Guardar estado deste operador", type="primary"):
                state["ultima_atualizacao"] = datetime.now().isoformat()
                state["parsed"] = {
                    "nome": parsed["nome"],
                    "nif": parsed["nif"],
                    "codigo_interno": parsed["codigo_interno"],
                    "data": parsed["data"],
                    "estado": parsed["estado"]
                }
                save_state(parsed["nif"] or "unknown", state)
                st.success("Estado guardado!")

        else:
            st.success("Não existem Não Conformidades. Este processo está **APTO**.")

        if parsed["informacoes"]:
            with st.expander("ℹ️ Informações / Obrigações (não bloqueiam certificação)"):
                for info in parsed["informacoes"]:
                    st.markdown(f"**Código {info['codigo']}**  \n{info['texto']}")

        # Exportar detalhe do operador selecionado
        st.markdown("---")
        st.markdown("#### Exportar detalhe deste operador")

        resumo_rows = []
        for nc in parsed["nao_conformidades"]:
            codigo = nc["codigo"]
            requisitos = nc["documentos_pedidos"] if nc["documentos_pedidos"] else ["Documento / evidência"]
            docs_por_req = state.get("docs_por_req", {}).get(codigo, {})

            for req in requisitos:
                ficheiros = docs_por_req.get(req, [])
                nomes = [Path(p).name for p in ficheiros]
                resumo_rows.append({
                    "NIF": parsed["nif"],
                    "Nome": parsed["nome"],
                    "Código Interno": parsed["codigo_interno"],
                    "Data": parsed["data"],
                    "Estado": parsed["estado"],
                    "Código NC": codigo,
                    "Descrição NC": nc["descricao"],
                    "Requisito": req,
                    "Documento submetido": "; ".join(nomes) if nomes else "—",
                    "Resolvida": "Sim" if state["resolvidas"].get(codigo) else "Não"
                })

        if not resumo_rows:
            resumo_rows.append({
                "NIF": parsed["nif"],
                "Nome": parsed["nome"],
                "Código Interno": parsed["codigo_interno"],
                "Data": parsed["data"],
                "Estado": "APTO",
                "Código NC": "—",
                "Descrição NC": "Sem não conformidades",
                "Requisito": "—",
                "Documento submetido": "—",
                "Resolvida": "—"
            })

        df = pd.DataFrame(resumo_rows)
        st.dataframe(df, use_container_width=True)

        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Detalhe")
        st.download_button(
            label="📥 Descarregar detalhe deste operador (Excel)",
            data=buffer.getvalue(),
            file_name=f"detalhe_{parsed['nif'] or 'operador'}_{parsed['data'] or 'sem_data'}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_detalhe"
        )

    except Exception as e:
        st.error(f"Erro ao processar os PDFs: {e}")
        st.exception(e)

else:
    st.info("👈 Carregue um ou vários relatórios PDF na barra lateral para começar.")
    st.markdown("""
    ### Como usar
    1. Carregue **um ou vários** Relatórios Resumo (MP6) da CERTIS
    2. Veja o **resumo geral** de todos os operadores (APTO / NÃO APTO e documentos em falta)
    3. Escolha um operador para ver o detalhe e fazer upload dos documentos
    4. Cada requisito de uma NC tem o seu próprio campo de upload
    5. Exporte o resumo geral ou o detalhe de cada operador em Excel

    **Nota:** As secções “Informações” (códigos 4, 26, 1.22…) **não** impedem a certificação.
    """)
