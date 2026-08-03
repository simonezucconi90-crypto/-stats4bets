create table if not exists public.matches (
id text primary key,
date text,time text,league text,match_name text,round_name text,market text,pick text,
selected_by_ale text,associated_method text,
prob_1 double precision,prob_x double precision,prob_2 double precision,
fair_odds double precision,opening_odds double precision,current_odds double precision,
c_aff text,flbk text,c_fb text,qra_qa text,qi_qa text,
allibramento_color text,allibramento_value double precision,allibramento_avg double precision,
allb text,mtr text,scl text,cal text,status text,
stake double precision,played_odds double precision,outcome text,final_score text,
gross_return double precision,profit double precision,
flag_1x2 integer default 0,flag_over_15 integer default 0,flag_over_25 integer default 0,
flag_under_25 integer default 0,flag_under_35 integer default 0,
flag_multigol_13 integer default 0,flag_multigol_14 integer default 0,
flag_formula4 integer default 0,flag_easy_over integer default 0,flag_super_over integer default 0
);
alter table public.matches enable row level security;
create policy "allow all for personal app" on public.matches for all using (true) with check (true);
