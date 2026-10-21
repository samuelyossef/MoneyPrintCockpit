"""Natural-language assistant chat route."""
from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError
from gateway.growth.api import store as growth_store
from gateway.growth.api import OpportunityScoreRequest, PlanningWorkflowRequest, YPPGoalRequest
from gateway.growth.api import evaluate_goal, score_opportunity, start_planning_workflow
from gateway.growth.store import GrowthStoreUnavailable

from gateway import main as m

router = APIRouter()


@router.post("/api/assistant/chat")
async def assistant_chat(input: m.AssistantChatInput) -> dict[str, Any]:
    channels = m.list_channels()
    channel_context = [
        {key: channel.get(key) for key in ("id", "name", "platform", "niche", "language", "status")}
        for channel in channels
    ]
    transcript = [{"role": item.role, "content": item.content} for item in input.messages[-12:]]
    prompt = (
        "Você é a assistente conversacional do MoneyPrinter Cockpit. Responda em português brasileiro, "
        "de modo direto e natural. Entenda o histórico e escolha uma ação somente quando o usuário a pedir "
        "ou quando ela for necessária para responder. Nunca invente métricas do canal. "
        "Ações disponíveis: reply (conversa e explicações), clarify (perguntar os dados que faltam), "
        "growth_goal (calcular metas YPP), growth_score (pontuar uma oportunidade de tema), "
        "growth_plan (criar um plano durável; só quando pedido explicitamente), script_proposal "
        "(propor ângulo antes de escrever; use quando pedirem um roteiro e ainda não houver proposta confirmada), "
        "short_script (gerar um roteiro de Short somente depois que o usuário confirmar a proposta em args.proposal), "
        "channels (listar canais), "
        "jobs (listar trabalhos recentes), workflow_status (consultar um workflow) e system_status "
        "(verificar a saúde do sistema). Não publique nem aprove conteúdo. Planos ficam em revisão humana. "
        "Para growth_goal, forneça args.metrics com subscribers, subscriber_target, qualified_watch_hours, "
        "watch_hour_target, qualified_shorts_views, shorts_view_target, days_remaining e, se conhecido, "
        "average_view_duration_minutes. Se faltar algum dado obrigatório, escolha clarify sem adivinhar. "
        "Para growth_score, forneça topic e dimensions com 12 notas de 0 a 100: demand, audience_fit, "
        "historical_retention, subscriber_conversion, expected_avd, evergreen, trend_velocity, monetization, "
        "stock_availability, competition, production_cost e saturation. Se estimar notas com a IA, marque "
        "args.estimated=true. Para growth_plan, forneça topic, ypp_goal (mesmas métricas) e opportunity "
        "(as 12 notas); nunca defina publicação automática. Para script_proposal, forneça subject. "
        "Para short_script, forneça subject e proposal com o texto confirmado pelo usuário. "
        "Para workflow_status, forneça workflow_id. Para ações que dependem de canal, use o canal selecionado "
        "ou um nome que corresponda exatamente à lista. Responda somente com um objeto JSON neste formato: "
        '{"action":"reply|clarify|growth_goal|growth_score|growth_plan|script_proposal|short_script|channels|jobs|workflow_status|system_status","message":"texto curto","args":{}}'
        + "\nCanal selecionado: " + json.dumps(input.channel_id, ensure_ascii=False)
        + "\nCanais disponíveis: " + json.dumps(channel_context, ensure_ascii=False)
        + "\nHistórico: " + json.dumps(transcript, ensure_ascii=False)
    )
    decision = m.parse_json_object(await m.llm_chat(prompt, json_mode=True, task="json", max_tokens=1600))
    action = str(decision.get("action") or "reply")
    message = str(decision.get("message") or "").strip()
    args = decision.get("args") if isinstance(decision.get("args"), dict) else {}

    if action in {"reply", "clarify"}:
        return {"action": action, "message": message or "Pode me contar um pouco mais sobre o que você precisa?"}
    if action == "channels":
        if not channels:
            return {"action": action, "message": "Ainda não há canais cadastrados. Conecte um canal na área Canais."}
        lines = [f"- {item.get('name')} ({item.get('platform')}) · {item.get('status')}" for item in channels]
        return {"action": action, "message": "Seus canais cadastrados:\n" + "\n".join(lines), "data": channel_context}
    if action == "jobs":
        with m.database() as db:
            jobs = [dict(row) for row in db.execute(
                "SELECT id, subject, state, progress, created_at FROM video_jobs "
                "WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 8"
            )]
        if not jobs:
            return {"action": action, "message": "Ainda não há vídeos em processamento ou no histórico."}
        lines = [f"- {item['subject']} · {item['state']} · {item['progress']}%" for item in jobs]
        return {"action": action, "message": "Trabalhos recentes:\n" + "\n".join(lines), "data": jobs}
    if action == "system_status":
        status = await m.health_status()
        return {"action": action, "message": (
            f"Estado do sistema: {status.get('status', 'indisponível')}. "
            f"Banco de dados: {'conectado' if status.get('database') else 'indisponível'}; "
            f"MoneyPrinter Turbo: {'conectado' if status.get('turbo') else 'indisponível'}."
        ), "data": status}
    if action == "workflow_status":
        try:
            workflow_id = UUID(str(args.get("workflow_id") or ""))
            workflow = growth_store.workflow(workflow_id)
        except (ValueError, GrowthStoreUnavailable) as exc:
            raise HTTPException(status_code=400 if isinstance(exc, ValueError) else 503, detail="Não foi possível consultar esse workflow.") from exc
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow não encontrado.")
        return {"action": action, "message": (
            f"Plano {workflow_id}: estado {workflow.get('state')} · etapa {workflow.get('current_step') or '—'}."
        ), "data": workflow}

    channel = m._assistant_channel(channels, input.channel_id, args)
    if not channel:
        detail = "Conecte um canal antes de continuar." if not channels else "Selecione um canal ou diga o nome exato dele."
        return {"action": "clarify", "message": detail}

    try:
        channel_id = UUID(str(channel["id"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="O canal selecionado tem um identificador inválido.") from exc

    if action == "growth_goal":
        try:
            goal_input = YPPGoalRequest(channel_id=channel_id, metrics=args.get("metrics") or {})
        except ValidationError:
            return {"action": "clarify", "message": "Para calcular seu gap, me passe os valores atuais e as metas de inscritos, horas qualificadas e visualizações de Shorts, além de quantos dias faltam. Pode escrever tudo em uma única frase."}
        result = evaluate_goal(goal_input)
        values = result["result"].model_dump(mode="json")
        return {"action": action, "data": values, "message": (
            f"Para {channel.get('name')}, faltam {values['subscribers']['gap']:,.0f} inscritos "
            f"({values['subscribers']['required_daily_velocity']:,.2f}/dia), "
            f"{values['watch_hours']['gap']:,.0f} horas qualificadas "
            f"({values['watch_hours']['required_daily_velocity']:,.2f}/dia) e "
            f"{values['shorts_views']['gap']:,.0f} visualizações qualificadas de Shorts. "
            "O cálculo considera apenas métricas orgânicas qualificadas."
        )}
    if action == "growth_score":
        try:
            score_input = OpportunityScoreRequest(
                channel_id=channel_id, topic=str(args.get("topic") or ""), dimensions=args.get("dimensions") or {},
            )
        except ValidationError:
            return {"action": "clarify", "message": "Me diga o tema da oportunidade. Posso estimar as dimensões para iniciar a avaliação e identificar claramente o resultado como estimativa."}
        result = score_opportunity(score_input)
        values = result["result"].model_dump(mode="json")
        estimate = " Esta é uma estimativa da IA, não uma medição de dados externos." if args.get("estimated") else ""
        return {"action": action, "data": values, "message": (
            f"O tema “{score_input.topic}” recebeu {values['score']}/100 "
            f"(versão {values['version']}).{estimate}"
        )}
    if action == "growth_plan":
        try:
            plan_input = PlanningWorkflowRequest(
                channel_id=channel_id, topic=str(args.get("topic") or ""),
                ypp_goal=args.get("ypp_goal") or {}, opportunity=args.get("opportunity") or {},
                production_target=args.get("production_target") or {"long": 1, "shorts": 3},
                publish_mode="review",
            )
        except ValidationError:
            return {"action": "clarify", "message": "Para montar o plano, preciso do tema, dos números atuais e das metas YPP e do prazo. Posso coletar esses dados com você, um passo de cada vez."}
        started = await start_planning_workflow(plan_input)
        return {"action": action, "data": started, "message": (
            f"Iniciei o plano para “{plan_input.topic}” no canal {channel.get('name')}. "
            f"Ele ficará em revisão humana. ID do workflow: {started['id']}."
        )}
    if action == "script_proposal":
        subject = str(args.get("subject") or "").strip()
        if not subject:
            return {"action": "clarify", "message": "Qual é o tema do vídeo?"}
        proposal = await m.script_proposal(m.ScriptProposalInput(
            subject=subject, format="short", language=str(channel.get("language") or "pt-BR"),
            channel_context=str(channel.get("niche") or ""),
        ))
        questions = "\n".join(f"- {item}" for item in proposal["audience_questions"])
        return {"action": action, "data": proposal, "message": (
            f"Proposta para “{subject}”:\n{proposal['brief']}\n\nPerguntas da audiência:\n{questions}\n\n"
            "Confirme este ângulo, ou diga o que mudar, para eu escrever o roteiro."
        )}
    if action == "short_script":
        subject = str(args.get("subject") or "").strip()
        proposal = str(args.get("proposal") or "").strip()
        if not subject:
            return {"action": "clarify", "message": "Qual é o tema do roteiro?"}
        if not proposal:
            return {"action": "clarify", "message": "Antes do roteiro, confirme o ângulo da proposta. Posso montar essa proposta agora."}
        result = await m.short_script(m.ShortScriptInput(
            subject=subject, language=str(channel.get("language") or "pt-BR"),
            voice_rate=float(channel.get("voice_rate") or 1.0), voice_name=str(channel.get("voice_name") or ""),
            script_prompt=str(args.get("style") or "")[:2000],
            system_prompt=str(channel.get("system_prompt") or "")[:4000],
            proposal=proposal,
            reference_brief=str(args.get("reference_brief") or "")[:1000],
        ))
        script = result["script"]
        return {"action": action, "data": result, "message": (
            f"Roteiro de Short sobre “{subject}” ({result['words']} palavras):\n\n{script}"
        )}
    return {"action": "reply", "message": message or "Ainda não consigo executar esse pedido pela conversa. Posso consultar canais e jobs, calcular metas e oportunidades, criar um plano de crescimento ou gerar um roteiro de Short."}
