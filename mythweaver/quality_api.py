from fastapi import APIRouter,Header
from . import quality,trials
from .command_catalog import request_key


def router(service,settings):
    api=APIRouter(prefix='/api/games/{game_id}')

    @api.get('/requirements')
    def requirements(game_id:str,key:str|None=None,revision:int|None=None):return quality.read(service,game_id,key,revision)

    @api.put('/requirements')
    def save_requirements(game_id:str,body:quality.DocumentPut,idempotency_key:str=Header()):
        return service.command(game_id,'requirements.put',body.model_dump(),request_key('requirements.put',idempotency_key))

    @api.post('/acceptance')
    async def acceptance(game_id:str,body:quality.AcceptanceRequest):return await quality.run_acceptance(service,settings,game_id,body.model_dump())

    @api.get('/trials')
    def trial_list(game_id:str):return trials.listing(service,game_id)

    @api.post('/trials')
    def trial_create(game_id:str,body:trials.TrialCreate):return trials.create(service,game_id,body.model_dump())

    @api.post('/trials/turn')
    async def trial_turn(game_id:str,body:trials.TrialTurn):return await trials.turn(service,settings,game_id,body.model_dump())

    @api.post('/trials/change')
    def trial_change(game_id:str,body:trials.TrialChange):return trials.change(service,game_id,body.model_dump())

    return api
