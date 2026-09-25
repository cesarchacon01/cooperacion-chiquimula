import os, json, re, sys
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = Path(__file__).resolve().parent
CATALOGO = json.loads((BASE/'catalogo_xlsform.json').read_text(encoding='utf-8'))
CHOICES = CATALOGO['choices']; LUGARES = CATALOGO['lugares_poblados']
MUNICIPIOS = ['Camotán','Chiquimula','Concepción las Minas','Esquipulas','Ipala','Jocotán','Olopa','Quezaltepeque','San Jacinto','San José la Arada','San Juan Ermita']

def label(lista, valor):
    if valor in (None,''): return ''
    return CHOICES.get(lista,{}).get(str(valor), str(valor))

def labels(lista, valor):
    if not valor: return []
    vals = valor if isinstance(valor,list) else str(valor).split()
    return [label(lista,v) for v in vals if v]

def find_value(obj, name, default=None):
    if not isinstance(obj,dict): return default
    if name in obj: return obj[name]
    for k,v in obj.items():
        if k.split('/')[-1] == name and not isinstance(v,(dict,list)): return v
    for v in obj.values():
        if isinstance(v,dict):
            got=find_value(v,name,None)
            if got is not None: return got
    return default

def find_repeat(obj, name):
    if not isinstance(obj,dict): return []
    if name in obj and isinstance(obj[name],list): return obj[name]
    for k,v in obj.items():
        if k.split('/')[-1] == name and isinstance(v,list): return v
    for v in obj.values():
        if isinstance(v,dict):
            got=find_repeat(v,name)
            if got: return got
    return []

def num(v):
    try: return int(float(v)) if v not in (None,'') else 0
    except: return 0

def cargar_coordenadas():
    p=BASE/'coordenadas_lugares.json'
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        print('ADVERTENCIA: coordenadas_lugares.json no pudo leerse:',e); return {}

COORDS=cargar_coordenadas()

def coord_for(code):
    c=COORDS.get(code,{})
    return c.get('lat'), c.get('lon')

def api_get(url, token):
    req=Request(url,headers={'Authorization':f'Token {token}','Accept':'application/json','User-Agent':'cooperacion-chiquimula-github-action'})
    with urlopen(req,timeout=90) as r: return json.loads(r.read().decode('utf-8'))

def descargar():
    token=os.environ.get('KOBO_TOKEN','').strip(); uid=os.environ.get('KOBO_ASSET_UID','').strip()
    if not token or not uid: raise RuntimeError('Faltan KOBO_TOKEN o KOBO_ASSET_UID en GitHub Secrets.')
    url=f'https://eu.kobotoolbox.org/api/v2/assets/{uid}/data/?format=json&limit=3000'
    data=api_get(url,token)
    resultados=data.get('results',data if isinstance(data,list) else [])
    # Paginación defensiva
    while isinstance(data,dict) and data.get('next'):
        data=api_get(data['next'],token); resultados.extend(data.get('results',[]))
    return resultados

def programa_desde(rep, actor):
    sectores=labels('sector',find_value(rep,'sector',''))
    if find_value(rep,'sector_otro'):
        sectores=[s for s in sectores if s!='Otro']+[str(find_value(rep,'sector_otro'))]
    modalidad=labels('modalidad',find_value(rep,'modalidad',''))
    if find_value(rep,'modalidad_otro'): modalidad=[x for x in modalidad if x!='Otra']+[str(find_value(rep,'modalidad_otro'))]
    fuentes=labels('fuente_financiamiento',find_value(rep,'fuente_financiamiento',''))
    if find_value(rep,'fuente_financiamiento_otro'): fuentes=[x for x in fuentes if x!='Otros']+[str(find_value(rep,'fuente_financiamiento_otro'))]
    mun_inst=labels('municipios',find_value(rep,'municipios_cobertura_institucional',''))
    mun_inst=[m for m in mun_inst if m!='Otro']
    if find_value(rep,'municipios_cobertura_institucional_otro'): mun_inst.append(str(find_value(rep,'municipios_cobertura_institucional_otro')))
    inst=labels('beneficiarios_institucionales',find_value(rep,'beneficiarios_institucionales',''))
    inst_counts={}
    inst_code_to_field={'Centros o puestos de salud':'bi_cant_centros_salud','Municipalidades':'bi_cant_municipalidades','COMRED (Coordinadora Municipal para la Reducción de Desastres)':'bi_cant_comred','COMUSAN (Comisión Municipal de Seguridad Alimentaria y Nutricional)':'bi_cant_comusan','Oficina Municipal de Juventud':'bi_cant_oficina_municipal_juventud','Dirección Municipal de Planificación (DMP)':'bi_cant_direccion_municipal_planificacion','Oficina Municipal de Desarrollo Económico Local':'bi_cant_oficina_municipal_desarrollo_economico','Oficina Municipal de Gestión Integral de Riesgo de Desastres (GIRD)':'bi_cant_oficina_municipal_gird','Oficina/Dirección Municipal de Seguridad Alimentaria y Nutricional (SAN)':'bi_cant_oficina_municipal_san'}
    for x in inst:
        if x=='Otra':
            otro=str(find_value(rep,'beneficiarios_institucionales_otro','Otro')); inst_counts[otro]=num(find_value(rep,'bi_cant_otro'))
        else: inst_counts[x]=num(find_value(rep,inst_code_to_field.get(x,''))) if inst_code_to_field.get(x) else 0
    comunidades=[]
    for c in find_repeat(rep,'cobertura_comunidad'):
        code=str(find_value(c,'lugar_poblado','') or '')
        meta=LUGARES.get(code,{})
        muni=label('municipios',meta.get('municipio_codigo',''))
        lugar=meta.get('lugar_poblado') or code
        lat,lon=coord_for(code)
        pob=labels('poblacion_meta',find_value(c,'poblacion_meta',''))
        cantidades={}
        for pc in ['mujeres','mujeres_embarazadas_lactantes','pueblos_indigenas','migrantes_retornados','personas_discapacidad','adultos_mayores','hogares_ninez_menor_5','familias','agricultores','estudiantes','docentes','padres_cuidadores']:
            if pc in str(find_value(c,'poblacion_meta','')).split(): cantidades[label('poblacion_meta',pc)]=num(find_value(c,'pm_cant_'+pc))
        if 'otro' in str(find_value(c,'poblacion_meta','')).split(): cantidades[str(find_value(c,'poblacion_meta_otro','Otro'))]=num(find_value(c,'pm_cant_otro'))
        acciones=labels('accion_comunidad',find_value(c,'accion_comunidad',''))
        ac_counts={}
        for ac in ['escuelas','organizaciones_comunitarias','cooperativas','grupos_auto_ahorro_prestamo','emprendimientos','colred','cuadrillas_incendios','cader_eca','viveros_forestales','sistemas_riego','infraestructura_productiva_agropecuaria']:
            if ac in str(find_value(c,'accion_comunidad','')).split(): ac_counts[label('accion_comunidad',ac)]=num(find_value(c,'ac_cant_'+ac))
        if 'otro' in str(find_value(c,'accion_comunidad','')).split(): ac_counts[str(find_value(c,'accion_comunidad_otro','Otro'))]=num(find_value(c,'ac_cant_otro'))
        comunidades.append({'codigo_lugar':code,'lugar_poblado':lugar,'municipio':muni,'clasificacion':meta.get('clasificacion'),'viviendas_catalogo':meta.get('viviendas'),'lat':lat,'lon':lon,'poblacion_meta':pob,'pm_cantidades':cantidades,'poblacion_total_estimada':max(cantidades.values(),default=0),'accion_comunidad':acciones,'ac_cantidades':ac_counts,'etnia':labels('etnia',find_value(c,'etnia','')),'grupo_etario':labels('grupo_etario',find_value(c,'grupo_etario','')),'area':label('area',find_value(c,'area',''))})
    return {'organizacion':actor['organizacion'],'tipo_actor':actor['tipo_actor'],'pais_origen':actor['pais_origen'],'contacto':actor['contacto'],'nombre_programa':str(find_value(rep,'nombre_programa','')),'tipo_atencion':label('tipo_atencion',find_value(rep,'tipo_atencion','')),'sector':sectores,'modalidad':modalidad,'fecha_inicio':str(find_value(rep,'fecha_inicio','') or ''),'tipo_duracion':label('tipo_duracion',find_value(rep,'tipo_duracion','')),'fecha_fin':str(find_value(rep,'fecha_fin','') or ''),'duracion_aproximada':str(find_value(rep,'duracion_aproximada_texto','') or ''),'estado_programa':label('estado_programa',find_value(rep,'estado_programa','')),'contraparte_gubernamental':str(find_value(rep,'contraparte_gubernamental','') or ''),'monto_rango':label('monto_rango',find_value(rep,'monto_rango','')),'monto_rango_codigo':str(find_value(rep,'monto_rango','') or ''),'fuente_financiamiento':fuentes,'espacios_coordinacion':labels('espacios_coordinacion',find_value(rep,'espacios_coordinacion','')),'disposicion_coordinar':label('disposicion_coordinar',find_value(rep,'disposicion_coordinar','')),'beneficiarios_institucionales':inst_counts,'municipios_cobertura_institucional':mun_inst,'comunidades':comunidades}

def construir(registros):
    programas=[]
    for r in registros:
        actor={'organizacion':str(find_value(r,'nombre_organizacion','') or ''),'tipo_actor':label('tipo_actor',find_value(r,'tipo_actor','')),'pais_origen':str(find_value(r,'pais_origen','') or ''),'contacto':str(find_value(r,'correo_contacto','') or '')}
        reps=find_repeat(r,'programa_proyecto')
        if not reps and find_value(r,'nombre_programa'): reps=[r]
        for rep in reps:
            p=programa_desde(rep,actor)
            if p['nombre_programa'] or p['organizacion']: programas.append(p)
    resumen={m:{'organizaciones':[],'total_programas':0,'comunidades_cubiertas':0,'comunidades_total_catalogo':0,'inversion_estimada':0,'sectores':{},'estado':{},'instituciones':{},'fuente_financiamiento':{}} for m in MUNICIPIOS}
    for meta in LUGARES.values():
        m=label('municipios',meta.get('municipio_codigo',''))
        if m in resumen: resumen[m]['comunidades_total_catalogo']+=1
    matriz={}; inst_res={}; pop_acc={}; dup={}
    rango_mid={'menos_500k':250000,'500k_2m':1250000,'2m_10m':6000000,'mas_10m':10000000}
    inv={'total_estimada':0,'programas_con_monto':0,'programas_sin_monto':0,'por_sector':{},'nota_metodologica':'Estimación técnica para visualización basada en puntos medios de rangos; no representa ejecución financiera auditada.'}
    for p in programas:
        munis=set([c['municipio'] for c in p['comunidades'] if c['municipio']] + p['municipios_cobertura_institucional'])
        mid=rango_mid.get(p['monto_rango_codigo'],0)
        if mid: inv['programas_con_monto']+=1; inv['total_estimada']+=mid
        else: inv['programas_sin_monto']+=1
        for s in p['sector']:
            matriz.setdefault(s,{})
            inv['por_sector'][s]=inv['por_sector'].get(s,0)+(mid/max(1,len(p['sector'])) if mid else 0)
            for m in munis: matriz[s][m]=matriz[s].get(m,0)+1
        for m in munis:
            if m not in resumen: continue
            rr=resumen[m]; rr['total_programas']+=1
            if p['organizacion'] and p['organizacion'] not in rr['organizaciones']: rr['organizaciones'].append(p['organizacion'])
            for s in p['sector']: rr['sectores'][s]=rr['sectores'].get(s,0)+1
            if p['estado_programa']: rr['estado'][p['estado_programa']]=rr['estado'].get(p['estado_programa'],0)+1
            for f in p['fuente_financiamiento']: rr['fuente_financiamiento'][f]=rr['fuente_financiamiento'].get(f,0)+1
            if mid: rr['inversion_estimada']+=mid/max(1,len(munis))
        for m in p['municipios_cobertura_institucional']:
            if m in resumen:
                for typ,n in p['beneficiarios_institucionales'].items(): resumen[m]['instituciones'][typ]=resumen[m]['instituciones'].get(typ,0)+n
        for typ,n in p['beneficiarios_institucionales'].items(): inst_res[typ]=inst_res.get(typ,0)+n
        for c in p['comunidades']:
            for po in c['poblacion_meta']:
                pop_acc.setdefault(po,{})
                for ac in c['accion_comunidad']: pop_acc[po][ac]=pop_acc[po].get(ac,0)+1
            for s in p['sector']:
                key=(c['codigo_lugar'],c['municipio'],s); dup.setdefault(key,set()).add(p['organizacion'])
    for m,rr in resumen.items():
        rr['organizaciones'].sort(); rr['comunidades_cubiertas']=len({c['codigo_lugar'] for p in programas for c in p['comunidades'] if c['municipio']==m})
    duplicidad=[{'lugar_poblado':LUGARES.get(k[0],{}).get('lugar_poblado',k[0]),'municipio':k[1],'sector':k[2],'organizaciones':sorted(v)} for k,v in dup.items() if len(v)>1]
    covered={c['codigo_lugar'] for p in programas for c in p['comunidades']}
    sin=[]
    for code,meta in LUGARES.items():
        if code in covered: continue
        lat,lon=coord_for(code)
        if lat is not None and lon is not None: sin.append({'codigo_lugar':code,'lugar_poblado':meta['lugar_poblado'],'municipio':label('municipios',meta['municipio_codigo']),'lat':lat,'lon':lon})
    return {'generado':datetime.now(timezone.utc).isoformat(),'nota':'Datos sincronizados automáticamente desde KoboToolbox. Base territorial del XLSForm: 996 lugares poblados.','programas':programas,'municipios':MUNICIPIOS,'resumen_municipio':resumen,'matriz_sector_municipio':matriz,'comunidades_sin_cobertura':sin,'duplicidad':duplicidad,'instituciones_resumen':inst_res,'matriz_poblacion_accion':pop_acc,'inversion':inv,'control':{'registros_kobo':len(registros),'programas_procesados':len(programas),'lugares_catalogo':len(LUGARES),'lugares_con_coordenadas':len(COORDS)}}

def main():
    try:
        regs=descargar(); print(f'Registros Kobo descargados: {len(regs)}')
        data=construir(regs)
        (BASE/'data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f"Programas/proyectos procesados: {len(data['programas'])}")
        print('data.json actualizado correctamente.')
    except Exception as e:
        print('ERROR:',e,file=sys.stderr); raise
if __name__=='__main__': main()
