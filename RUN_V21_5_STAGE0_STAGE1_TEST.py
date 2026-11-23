from services.v215_surrealspeed_engine import stage0_filter, classify_stage1_tasks, commercial_probe_text

fail=[]
def ck(name, cond):
    print(('PASS' if cond else 'FAIL'), name)
    if not cond: fail.append(name)

ck('OTP removed at Stage 0', stage0_filter('Google','Verification code','OTP code 123456',[])['decision']=='CERTAIN_IRRELEVANT')
ck('Instagram notification removed at Stage 0', stage0_filter('notify@instagram.com','New login','Security alert new sign-in',[])['decision']=='CERTAIN_IRRELEVANT')
ck('Quotation overrides noisy sender', stage0_filter('notify@google.com','Quotation spreadsheet shared','Please review',[{'filename':'Quotation.xlsx'}])['decision']=='CONTINUE')
ck('Business attachment is not blindly dropped', stage0_filter('alerts@example.com','Account alert','Security alert',[{'filename':'supplier_offer.pdf'}])['decision']=='CONTINUE')

tasks=classify_stage1_tasks({'attachments':[{'filename':'a.pdf','attachmentId':'1'},{'filename':'b.xlsx','attachmentId':'2'},{'filename':'c.docx','attachmentId':'3'},{'filename':'d.jpg','attachmentId':'4'}]})
lanes=[t['lane'] for t in tasks]
ck('Stage 1 always creates TEXT body lane', lanes[0]=='TEXT')
ck('PDF segregated', 'PDF' in lanes)
ck('XLSX segregated to TABLE', 'TABLE' in lanes)
ck('DOCX segregated to DOC', 'DOC' in lanes)
ck('Image segregated to OCR', 'OCR' in lanes)

ck('Sampling rate is not a price', not commercial_probe_text('Sampling Rate 2 Times / Second\nAccuracy ±3%\nRange 0-200000 Lux')['commercial'])
ck('INR quotation amount is commercial', commercial_probe_text('QUOTATION\nSDmatic 2 Analyzer INR 3,699,637.20 1.00')['commercial'])
ck('Explicit calibration charge is commercial', commercial_probe_text('Calibration of Sound Level Meter =Rs. 1,000.00')['commercial'])
ck('Company profile without money is skipped from deep price work', not commercial_probe_text('Company Profile\nNABL Accredited Laboratory\nISO/IEC 17025')['commercial'])

print('\nV21.5 STAGE 0/1 TEST:', 'PASSED' if not fail else 'FAILED')
raise SystemExit(1 if fail else 0)
