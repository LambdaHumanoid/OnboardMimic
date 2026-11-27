#include "../../../backends/sonic/deploy/src/g1/g1_deploy_onnx_ref/include/reference_replay.hpp"
#include <cassert>
#include <iostream>
#include <fstream>
using namespace sonic_replay;
namespace sonic_replay {
struct ExchangeTest {
 static void run(std::shared_ptr<const Plan> plan,const Feedback& feedback){
  Exchange e;uint64_t now=2;
  auto command=[&](const std::string& action,uint64_t sequence=0){
   auto c=std::make_shared<Exchange::Command>();c->action=action;c->plan=plan;c->run=123;c->sequence=sequence;
   e.submit(c);auto t=e.tick(now,true,feedback);assert(c->state==2&&c->error.empty());return t;
  };
  command("load");command("start");uint64_t sequence=0;
  for(now=20000002;now<=3200000002;now+=20000000)command("renew",++sequence);
  assert(e.snapshot().cursor==0);
  // Hold BOTH management locks on another thread. Neither lock may suppress
  // a control tick. This deterministically exercises the old failure mechanism.
  std::atomic<bool> held{false},release{false};
  std::thread manager([&]{std::scoped_lock l(e.inbox_mutex_,e.snapshot_mutex_);held=true;while(!release)std::this_thread::yield();});
  while(!held)std::this_thread::yield();
  for(int i=1;i<=3;++i,now+=20000000){auto t=e.tick(now,true,feedback);assert(!t.stop&&t.frame==uint64_t(i));}
  release=true;manager.join();
  auto t=command("renew",++sequence);assert(t.frame==4&&!t.stop);
  auto view=e.snapshot();assert(view.status()["schedule"]["recent"].empty());assert(view.command_deferrals==3&&view.snapshot_deferrals==3&&view.schedule_shift_ns==0);
  // Pending requests cancelled by the transport must never execute later.
  auto cancel=std::make_shared<Exchange::Command>();cancel->action="stop";cancel->run=123;cancel->state=3;e.submit(cancel);
  now+=20000000;assert(!e.tick(now,true,feedback).stop);
  // Independent watchdog stops even if the control callback never runs again.
  e.watch(now+lease_ns);assert(e.stopping());assert(e.snapshot().reason=="executor_timeout");
  assert(e.tick(now+lease_ns,true,feedback).stop);
  auto fault=e.snapshot();assert(fault.terminal&&fault.recent_count);
  fault.recent_count=16;for(auto& row:fault.recent)row={UINT64_MAX,UINT64_MAX,UINT64_MAX,UINT64_MAX,UINT64_MAX,UINT64_MAX,UINT64_MAX,UINT64_MAX,UINT64_MAX};
  assert(fault.status().dump().size()<8192);
 }
};
}
int main(int argc,char** argv){
 assert(planner_return_admissible(true,true,0));
 assert(!planner_return_admissible(true,true,1));
 assert(!planner_return_admissible(true,true,2));
 assert(!planner_return_admissible(true,false,0));
 assert(!planner_return_admissible(false,true,0));
 Feedback feedback;feedback.fresh=true;feedback.gravity={0,0,-1};
 if(argc==3&&std::string(argv[1])=="--schedule"){
  std::ifstream input(argv[2]);auto p=parse(Json::parse(input));feedback.gravity=p->start_gravity;
  for(int jitter_case=0;jitter_case<3;++jitter_case){
   Core c;c.load(p);c.arm(9,1);size_t confirmed=0;uint64_t last=UINT64_MAX,playing_ticks=0;
   for(uint64_t tick=0;tick<7000;++tick){
    uint64_t now=10000000+tick*20000000+(jitter_case==1?(tick%3)*2000000:0);
    if(jitter_case==2&&c.play_start){++playing_ticks;if(playing_ticks==1)now-=1000000;if(playing_ticks==369)now+=12000000;}
    c.renew(9,tick+1,now);auto t=c.tick(now,true,feedback);
    if(t.consumed&&t.frame!=last){assert(t.frame==confirmed);++confirmed;last=t.frame;}
    if(confirmed==p->frames.size()&&!c.finish_requested)c.finish(9);
    if(c.return_ready){c.release(9);assert(!c.tick(now+20000000,true,feedback).stop);break;}
    if(t.stop){std::cerr<<Json({{"jitter_case",jitter_case},{"confirmed",confirmed},{"reason",c.reason},{"tick",tick}}).dump()<<std::endl;}
    assert(!t.stop);
   }
   assert(confirmed==p->frames.size());
   std::cout<<Json({{"frames",confirmed},{"jitter_case",jitter_case},{"reason",c.reason},{"scheduled_ns",p->frames.back().due},{"source_ns",p->frames.back().offset}}).dump()<<std::endl;
  }
  return 0;
 }
 if(argc==2){
  // Software-only integration probe: no SDK, decoder, writer or motor socket.
  Service service(argv[1],Json::object());auto began=sonic_consumption::mono();
  while(sonic_consumption::mono()-began<10000000000ULL){
   auto tick=service.tick(sonic_consumption::mono(),true,feedback);
   if(tick.consumed)std::cout<<Json({{"frame",tick.frame},{"token0",tick.token[0]},{"run",tick.run}}).dump()<<std::endl;
   if(service.status()["state"]=="completed"){std::cout<<"PLANNER_HANDOFF"<<std::endl;return 0;}
   if(tick.stop||service.stopping()){std::cout<<"STOPPED_OUTPUT"<<std::endl;return 0;}
   std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }
  return 2;
 }

 Json j={{"entry_protocol",3},{"period_ns",20000000},{"start_q",std::vector<double>(29,0.)},{"start_gravity",{0.,0.,-1.}},{"frames",Json::array()}};
 for(int i=0;i<4;++i)j["frames"].push_back({{"offset_ns",uint64_t(i)*20000000},{"token",std::vector<double>(64,double(i))}});
 { // A continuous replay longer than two minutes retains every token.
 auto long_plan=j;long_plan["frames"]=Json::array();
 for(uint64_t i=0;i<6002;++i)long_plan["frames"].push_back({{"offset_ns",i*20000000},{"token",std::vector<double>(64,0.)}});
 auto lp=parse(long_plan);assert(lp->frames.size()==6002&&lp->frames.back().due==120020000000ULL);
 Core long_core;long_core.load(lp);long_core.arm(1000,1);size_t seen=0;
 for(uint64_t tick=0;tick<6300;++tick){auto now=2+tick*20000000;long_core.renew(1000,tick+1,now);auto out=long_core.tick(now,true,feedback);assert(!out.stop);
  if(out.consumed){assert(out.frame==seen);++seen;}
  if(seen==6002){long_core.finish(1000);break;}}
 assert(seen==6002);
 }
 // Manual return freezes the selected token, including entry before frame zero.
 for(uint64_t cancel_at: {400000002ULL,1600000002ULL,3220000002ULL}){
  Core back;back.load(parse(j));back.arm(71,1);uint64_t n=0;Tick last;
  for(uint64_t now=2;now<=cancel_at;now+=20000000){back.renew(71,++n,now);last=back.tick(now,true,feedback);assert(!last.stop);}
  auto cursor=back.cursor;back.cancel_to_planner(71);
  for(uint64_t now=cancel_at+20000000;now<cancel_at+2600000000ULL;now+=20000000){
   back.renew(71,++n,now);auto out=back.tick(now,true,feedback);
   assert(!out.stop&&out.returning&&!out.consumed&&back.cursor==cursor);
   assert(out.token==back.plan->frames[cursor].token);
   if(cancel_at<warm_ns)assert(out.planner_blend==1.);
   if(back.return_ready){back.release(71);break;}
  }
  assert(back.completed&&!back.terminal);back.load(parse(j));assert(!back.operator_return);
 }
 auto p=parse(j);Core c;c.load(p);assert(!c.tick(1,true).external);c.arm(99,1);
 auto t=c.tick(2,true);assert(t.begin&&!t.external);uint64_t seq=0;
 for(uint64_t now=20000002;now<3200000002;now+=20000000){c.renew(99,++seq,now);t=c.tick(now,true,feedback);assert(!t.stop&&!t.consumed);}
 c.renew(99,++seq,3200000002);t=c.tick(3200000002,true,feedback);assert(t.consumed&&t.frame==0&&t.token[0]==0);
 t=c.tick(3220000002,true,feedback);assert(t.frame==1&&t.token[0]==1);
 t=c.tick(3240000002,true,feedback);assert(t.frame==2);t=c.tick(3260000002,true,feedback);assert(t.frame==3);
 c.finish(99);
 for(uint64_t now=3280000002;now<=5500000002;now+=20000000){c.renew(99,++seq,now);t=c.tick(now,true,feedback);assert(!t.stop&&t.returning&&!t.consumed);}
 assert(c.return_ready);c.release(99);assert(c.completed&&!c.armed&&!c.tick(5520000002,true,feedback).stop);
 c.load(p);c.arm(100,5540000002);assert(c.phase=="loaded"&&!c.finish_requested);c.stop("operator_stop");
 bool rejected=false;try{c.renew(99,++seq,3180000002);}catch(...){rejected=true;}assert(rejected);
 Core timeout;timeout.load(p);timeout.arm(1,1);timeout.watch(500000001);assert(timeout.terminal&&timeout.reason=="executor_timeout");
 Core stall;stall.load(p);stall.arm(2,1);stall.tick(2,true);
 // Heartbeat alone cannot hide a frozen control loop.
 stall.renew(2,1,400000001);stall.watch(500000002);assert(stall.terminal);
 Core wrong;wrong.load(p);wrong.arm(3,1);assert(wrong.tick(2,false).stop);
 Core mismatch;mismatch.load(p);mismatch.arm(4,1);mismatch.tick(2,true,feedback);
 Feedback moving=feedback;moving.dq[0]=.7;
 for(uint64_t now=20000002;now<=6000000002;now+=20000000){mismatch.renew(4,now,now);t=mismatch.tick(now,true,moving);assert(!t.consumed);}
 assert(t.stop&&mismatch.reason=="start_pose_transition_timeout");
 Feedback bad=feedback;bad.fresh=false;assert(!aligned(*p,bad));bad=feedback;bad.dq[0]=.61;assert(!aligned(*p,bad));
 bad=feedback;bad.q[0]=.33;bad.target[0]=.77;assert(aligned(*p,bad)); // state is not a servo target
 bad=feedback;bad.gravity[0]=.16;assert(!aligned(*p,bad));bad=feedback;bad.q[0]=NAN;assert(!aligned(*p,bad));
 Core settling;settling.load(p);settling.arm(5,1);settling.tick(2,true,feedback);
 for(uint64_t now=20000002;now<=3400000002;now+=20000000){
  settling.renew(5,now,now);
  // A single moving observation interrupts the continuous confirmation.
  t=settling.tick(now,true,now==3100000002?moving:feedback);
  if(now<3320000002)assert(!t.consumed);
  else {assert(t.consumed&&t.frame==(now-3320000002)/20000000);if(t.frame==3)break;}
 }
 Core cancel;cancel.load(p);cancel.arm(6,1);cancel.tick(2,true,feedback);
 cancel.stop("operator_stop");assert(cancel.tick(3,true,feedback).stop&&!cancel.selected);
 // Nonuniform source timestamps do not alter sequential 50Hz playback.
 // A real 80ms control stall still fails.
 Json uneven=j;uneven["frames"]=Json::array();
 for(int i=0;i<20;++i)uneven["frames"].push_back({{"offset_ns",i==1?42000000:i*20000000+(i>1?5000000:0)},{"token",std::vector<double>(64,double(i))}});
 auto u=parse(uneven);assert(u->frames[1].offset==42000000&&u->frames[1].due==20000000&&u->frames[2].due==40000000);
 Core lag;lag.load(u);lag.arm(7,1);lag.tick(2,true,feedback);
 for(uint64_t now=20000002;now<=3200000002;now+=20000000){lag.renew(7,now,now);t=lag.tick(now,true,feedback);}
 assert(t.consumed&&t.frame==0);
 t=lag.tick(3220000002,true,feedback);assert(t.consumed&&t.frame==1&&!t.stop);
 t=lag.tick(3240000002,true,feedback);assert(t.consumed&&t.frame==2&&!t.stop);
 t=lag.tick(3320000002,true,feedback);assert(t.stop&&lag.reason=="schedule_overrun");
 // Early polls do not advance before the half-period boundary, and repeated
 // polls cannot consume multiple future tokens. The 30ms late limit is exact.
 for(uint64_t extra: {0ULL,1ULL}){
  Core edge;edge.load(u);edge.arm(90,1);edge.tick(2,true,feedback);
  for(uint64_t now=20000002;now<=3200000002;now+=20000000){edge.renew(90,now,now);edge.tick(now,true,feedback);}
  t=edge.tick(3209000002,true,feedback);assert(t.frame==0&&!t.stop);
  t=edge.tick(3210000002,true,feedback);assert(t.frame==1&&!t.stop);
  t=edge.tick(3210000002,true,feedback);assert(t.frame==1&&!t.stop);
  Core boundary;boundary.load(u);boundary.arm(91,1);boundary.tick(2,true,feedback);
  for(uint64_t now=20000002;now<=3200000002;now+=20000000){boundary.renew(91,now,now);boundary.tick(now,true,feedback);}
  t=boundary.tick(3250000002+extra,true,feedback);
  assert(t.stop==bool(extra));
  if(extra){assert(boundary.reason=="schedule_overrun");}
  else {assert(t.frame==1);assert(boundary.schedule_lateness_ns==30000000);}

 }
 // Return is still leased, bounded and fail-closed until explicit release.
 for(int scenario=0;scenario<4;++scenario){
  Core back;back.load(p);back.arm(88,1);back.tick(2,true,feedback);uint64_t n=0;
  bool early=false;try{back.release(88);}catch(...){early=true;}assert(early);
  for(uint64_t now=20000002;now<=3260000002;now+=20000000){back.renew(88,++n,now);back.tick(now,true,feedback);}
  back.finish(88);
  if(scenario==0){back.watch(3760000002);assert(back.terminal&&back.reason=="executor_timeout");continue;}
  for(uint64_t now=3280000002;now<=9280000002;now+=20000000){
   back.renew(88,++n,now);t=back.tick(now,true,scenario==1?moving:feedback);
   if(scenario==2&&back.return_ready){back.tick(now+20000000,true,moving);assert(!back.return_ready);bool denied=false;try{back.release(88);}catch(...){denied=true;}assert(denied);break;}
   if(scenario==3){t=back.tick(now+80000000,true,feedback);assert(t.stop&&back.reason=="schedule_overrun");break;}
  }
  if(scenario==1)assert(back.terminal&&back.reason=="planner_return_timeout");
 }
 // Two isolated missed callback periods used to leave 40ms permanent backlog.
 // Every source token now remains ordered, with bounded phase correction.
 Json long_plan=j;long_plan["frames"]=Json::array();
 for(int i=0;i<400;++i)long_plan["frames"].push_back({{"offset_ns",uint64_t(i)*20000000},{"token",std::vector<double>(64,double(i))}});
 auto longp=parse(long_plan);
 for(int scenario=0;scenario<5;++scenario){
  Core jitter;jitter.load(longp);jitter.arm(71,1);int playing=0;uint64_t last=UINT64_MAX,confirmed=0;
  for(uint64_t now=2;now<12000000000ULL;now+=20000000){
   jitter.renew(71,now,now);
   if(jitter.play_start){++playing;
    bool miss=(scenario==0||scenario==4)?(playing==5||playing==10):scenario==1?(playing==5||playing==10||playing==15):scenario==2?(playing%55==5):false;
    if(miss)continue;
    if(scenario==3&&playing==5)now+=60000000;
   }
   t=jitter.tick(now+(scenario==4&&playing>=11?6000:0),true,feedback);
   if(t.stop){assert(scenario!=0&&scenario!=4&&jitter.reason=="schedule_overrun");break;}
   if(t.consumed&&t.frame!=last){assert(t.frame==confirmed&&t.token[0]==double(confirmed));++confirmed;last=t.frame;}
   if(confirmed==longp->frames.size()){assert(scenario==0||scenario==4);break;}
  }
  if(scenario==0)assert(confirmed==400&&jitter.schedule_shift_ns==40000000&&jitter.rebase_count==2);
  if(scenario>0&&scenario<4)assert(jitter.terminal);
  if(scenario==4)assert(confirmed==400&&jitter.schedule_shift_ns==40006000);
  if(scenario==1)assert(jitter.overrun_limit=="window_shift");
  if(scenario==2)assert(jitter.schedule_shift_ns==100000000&&jitter.overrun_limit=="total_shift");
 }
 ExchangeTest::run(longp,feedback);
 j["frames"][1]["offset_ns"]=0;rejected=false;try{parse(j);}catch(...){rejected=true;}assert(rejected);
}
